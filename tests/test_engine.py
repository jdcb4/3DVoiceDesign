import json
import time

import pytest
import trimesh

from voicedesign.engine import ROOT, Engine
from voicedesign.storage import Store, atomic_write


def wait(engine, design_id):
    deadline = time.monotonic() + 100
    while time.monotonic() < deadline:
        state = engine.refresh(design_id)
        if state["status"] != "building":
            return state
        time.sleep(0.1)
    raise AssertionError("Build did not finish")


@pytest.fixture
def engine(tmp_path):
    result = Engine(Store(tmp_path / "designs"), tmp_path / "cache")
    yield result
    result.close()


@pytest.mark.parametrize(
    "template,bounds",
    [("mounting-plate", [80, 50, 6]), ("enclosure", [80, 55, 25]), ("turned-knob", [36, 36, 22])],
)
def test_starters_are_valid_solids_with_watertight_stl_and_step(engine, template, bounds):
    design_id = engine.store.create(template, ROOT / "templates" / template)
    state = wait(engine, design_id)
    assert state["status"] == "ready", state["error"]
    assert state["result"]["stats"]["bounds"] == pytest.approx(bounds)
    assert state["result"]["stats"]["solids"] == 1
    stl = engine.artifact(design_id, state["fingerprint"], "model.stl")
    mesh = trimesh.load_mesh(stl)
    assert mesh.is_watertight
    assert mesh.is_winding_consistent
    assert mesh.volume > 0
    assert mesh.extents == pytest.approx(bounds, abs=0.1)
    step = engine.artifact(design_id, state["fingerprint"], "model.step")
    assert "ISO-10303-21" in step.read_text()
    for feature in state["result"]["features"]:
        data = json.loads(
            engine.artifact(
                design_id, state["fingerprint"], f"feature-{feature['index']}.json"
            ).read_text()
        )
        assert data["positions"] and data["indices"] and data["edges"]


def test_failed_edit_blocks_stale_exports_and_recovers_after_source_fix(engine):
    design_id = engine.store.create("Plate", ROOT / "templates" / "mounting-plate")
    first = wait(engine, design_id)
    assert first["status"] == "ready", first["error"]
    _, code, _ = engine.store.read(design_id)
    path = engine.store.path(design_id) / "model.py"
    atomic_write(path, "def build(p):\n    raise ValueError('intentional invalid geometry')\n")
    broken = wait(engine, design_id)
    assert broken["status"] == "error"
    assert "intentional invalid geometry" in broken["error"]
    with pytest.raises(ValueError):
        engine.artifact(design_id, first["fingerprint"], "model.stl")
    atomic_write(path, code)
    recovered = wait(engine, design_id)
    assert recovered["status"] == "ready", recovered["error"]
    assert recovered["result"]["stats"]["bounds"] == [80, 50, 6]


def test_parameter_change_changes_geometry(engine):
    design_id = engine.store.create("Plate", ROOT / "templates" / "mounting-plate")
    first = wait(engine, design_id)
    engine.store.update_parameters(design_id, {"width": 100}, first["fingerprint"])
    changed = wait(engine, design_id)
    assert changed["status"] == "ready", changed["error"]
    assert changed["result"]["stats"]["bounds"] == [100, 50, 6]
    assert changed["result"]["stats"]["volume"] > first["result"]["stats"]["volume"]


def test_blank_design_builds_then_gains_geometry_and_can_return_to_blank(engine):
    design_id = engine.store.create("Blank")
    _, blank_source, _ = engine.store.read(design_id)
    blank = wait(engine, design_id)
    assert blank["status"] == "ready", blank["error"]
    assert blank["result"] == {"features": [], "stats": None, "empty": True}
    with pytest.raises(ValueError, match="blank"):
        engine.artifact(design_id, blank["fingerprint"], "model.stl")
    source_path = engine.store.path(design_id) / "model.py"
    atomic_write(
        source_path,
        "import cadquery as cq\ndef build(p):\n    return cq.Workplane('XY').box(10, 20, 3)\n",
    )
    solid = wait(engine, design_id)
    assert solid["status"] == "ready", solid["error"]
    assert solid["result"]["stats"]["bounds"] == [10, 20, 3]
    assert engine.artifact(design_id, solid["fingerprint"], "model.stl").is_file()
    atomic_write(source_path, blank_source)
    empty_again = wait(engine, design_id)
    assert empty_again["result"]["empty"]
    with pytest.raises(ValueError):
        engine.artifact(design_id, solid["fingerprint"], "model.stl")
    with pytest.raises(ValueError, match="blank"):
        engine.artifact(design_id, empty_again["fingerprint"], "model.step")
    # Only an explicit Model([]) is blank; accidentally returning a sketch is still an error.
    atomic_write(
        source_path,
        "import cadquery as cq\ndef build(p):\n    return cq.Workplane('XY').circle(10)\n",
    )
    assert wait(engine, design_id)["status"] == "error"


def test_runaway_source_times_out(engine):
    design_id = engine.store.create("Loop", ROOT / "templates" / "mounting-plate")
    atomic_write(engine.store.path(design_id) / "model.py", "def build(p):\n    while True: pass\n")
    engine.timeout = 3
    state = wait(engine, design_id)
    assert state["status"] == "error"
    assert "exceeded 3 seconds" in state["error"]


def test_loft_sweep_pattern_mirror_and_booleans_are_available(engine):
    design_id = engine.store.create("Operations", ROOT / "templates" / "mounting-plate")
    source = """import cadquery as cq
from voicedesign.cad import Feature, Model
def build(p):
    loft = cq.Workplane("XY").circle(10).workplane(offset=20).circle(6).loft()
    path = cq.Workplane("XZ").spline([(0, 0), (0, 10), (10, 20)])
    sweep = cq.Workplane("XY").circle(2).sweep(path)
    pattern = cq.Workplane("XY").box(40, 40, 6).faces(">Z").workplane().rarray(20, 20, 2, 2).hole(4)
    block = cq.Workplane("XY").box(10, 10, 10)
    added = block.union(cq.Workplane("XY").box(10, 10, 10).translate((5, 0, 0)))
    removed = added.cut(cq.Workplane("XY").circle(2).extrude(20, both=True))
    common = added.intersect(block.translate((4, 0, 0)))
    mirrored = block.translate((20, 0, 0)).mirror("YZ")
    return Model([Feature(name, shape) for name, shape in
        [("Loft", loft), ("Sweep", sweep), ("Pattern", pattern), ("Union", added),
         ("Cut", removed), ("Intersect", common), ("Mirror", mirrored)]])
"""
    atomic_write(engine.store.path(design_id) / "model.py", source)
    state = wait(engine, design_id)
    assert state["status"] == "ready", state["error"]
    assert len(state["result"]["features"]) == 7
    assert all(f["volume"] > 0 and f["solids"] == 1 for f in state["result"]["features"])
