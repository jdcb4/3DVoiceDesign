"""Recovery and publication guarantees for the reusable CAD process boundary."""

import json
import os
import sys
import threading
import time

import pytest

from voicedesign.engine import Engine
from voicedesign.storage import BLANK_SOURCE, Conflict, Store, atomic_write


def box_features(width=20, height=30, depth=4):
    return [
        {
            "id": "outline",
            "type": "profile",
            "params": {"shape": "rectangle", "width": width, "height": height},
        },
        {"id": "body", "type": "extrude", "params": {"profile": "outline", "distance": depth}},
    ]


@pytest.fixture
def runtime(tmp_path):
    engine = Engine(Store(tmp_path / "designs"), tmp_path / "cache")
    design_id = engine.store.create("Runtime test")
    yield engine, design_id
    engine.close()


def candidate(runtime, features, fingerprint="test-candidate"):
    engine, design_id = runtime
    doc, _, _ = engine.store.read(design_id)
    doc = doc.model_copy(update={"features": features})
    return engine.build_candidate(design_id, BLANK_SOURCE, doc, fingerprint)


def test_structured_builds_reuse_kernel_without_geometry_or_parameter_leakage(runtime):
    first = candidate(runtime, box_features())
    second = candidate(runtime, box_features(9, 8, 7))
    blank = candidate(runtime, [])
    third = candidate(runtime, box_features())
    assert all(state["status"] == "ready" for state in (first, second, blank, third))
    assert first["result"]["stats"]["bounds"] == [20, 30, 4]
    assert second["result"]["stats"]["bounds"] == [9, 8, 7]
    assert blank["result"] == {"empty": True, "features": [], "stats": None}
    assert third["result"] == first["result"]
    assert first["timings"]["worker_pid"] == third["timings"]["worker_pid"]
    assert first["timings"]["import_seconds"] > 0
    assert second["timings"]["import_seconds"] == 0
    assert second["timings"]["worker_reused"]
    assert first["result"]["features"][0]["id"] == "body"
    mesh = json.loads((first["directory"] / "feature-0.json").read_text())
    assert mesh["id"] == "body"
    for phase in ("queue", "import", "kernel", "validation", "mesh", "export", "process"):
        assert second["timings"][f"{phase}_seconds"] >= 0


def test_candidate_failure_does_not_publish_and_worker_recovers(runtime):
    engine, design_id = runtime
    initial_doc, initial_source, initial_revision = engine.store.read(design_id)
    history = engine.store.history(design_id)
    good = candidate(runtime, box_features())
    invalid = box_features()
    invalid[1]["params"]["distance"] = 0
    broken = candidate(runtime, invalid)
    recovered = candidate(runtime, box_features())
    assert broken["status"] == "error"
    assert broken["feature_errors"][0]["feature_id"] == "body"
    assert recovered["status"] == "ready"
    assert good["timings"]["worker_pid"] == recovered["timings"]["worker_pid"]
    assert design_id not in engine.states
    assert engine.store.read(design_id) == (initial_doc, initial_source, initial_revision)
    assert engine.store.history(design_id) == history


def test_adopt_requires_matching_revision_and_does_not_rebuild(runtime, monkeypatch):
    engine, design_id = runtime
    doc, source, fingerprint = engine.store.read(design_id)
    state = engine.build_candidate(design_id, source, doc, fingerprint)
    assert state["status"] == "ready", state["error"]
    engine.adopt(design_id, state)

    def unexpected_build(*args, **kwargs):
        pytest.fail("An adopted exact-revision candidate must not build again")

    monkeypatch.setattr(engine.pool, "submit", unexpected_build)
    assert engine.refresh(design_id)["status"] == "ready"
    with pytest.raises(Conflict):
        engine.adopt(design_id, {**state, "fingerprint": "stale"})
    with pytest.raises(ValueError, match="successfully verified"):
        engine.adopt(design_id, {**state, "status": "error"})


@pytest.mark.parametrize("failure", ["crash", "timeout"])
def test_worker_crash_or_timeout_fails_request_and_next_build_recovers(
    runtime, monkeypatch, failure
):
    import voicedesign.engine as engine_module

    engine, _ = runtime
    original_popen = engine_module.subprocess.Popen
    # Replace only the external process in this test. A protocol-compatible
    # process dies/hangs after accepting a real request, without product test hooks.
    fault = "os._exit(51)" if failure == "crash" else "time.sleep(100)"
    script = (
        "import json, os, sys, time\n"
        "print(json.dumps({'ready':True,'import_seconds':0}),flush=True)\n"
        "sys.stdin.readline()\n" + fault + "\n"
    )

    def faulty_process(*args, **kwargs):
        return original_popen([sys.executable, "-u", "-c", script], **kwargs)

    engine.timeout = 0.5
    with monkeypatch.context() as patch:
        patch.setattr(engine_module.subprocess, "Popen", faulty_process)
        failed = candidate(runtime, box_features())
    assert failed["status"] == "error"
    assert ("exceeded" if failure == "timeout" else "exited unexpectedly") in failed["error"]
    assert engine.worker.process is None
    engine.timeout = 30
    recovered = candidate(runtime, box_features())
    assert recovered["status"] == "ready", recovered["error"]
    assert not recovered["timings"]["worker_reused"]


def test_worker_recycles_and_custom_source_cannot_poison_warm_kernel(runtime):
    engine, design_id = runtime
    engine.worker.recycle_after = 2
    first = candidate(runtime, box_features())
    doc, _, _ = engine.store.read(design_id)
    doc = doc.model_copy(update={"features": None})
    source = """import os, sys, cadquery as cq
def build(p):
    os.environ['VOICEDESIGN_TEST_POISON'] = 'yes'
    sys.path.clear()
    cq.test_poison = True
    print('This output must not be interpreted as a worker protocol message')
    return cq.Workplane('XY').box(3, 4, 5)
"""
    isolated = engine.build_candidate(design_id, source, doc, "isolated")
    second = candidate(runtime, box_features(11, 12, 13))
    third = candidate(runtime, box_features())
    assert isolated["status"] == "ready", isolated["error"]
    assert isolated["timings"]["worker_mode"] == "isolated"
    assert "VOICEDESIGN_TEST_POISON" not in os.environ
    assert first["timings"]["worker_pid"] == second["timings"]["worker_pid"]
    assert first["timings"]["worker_pid"] != third["timings"]["worker_pid"]
    assert second["result"]["stats"]["bounds"] == [11, 12, 13]
    assert third["result"]["stats"]["bounds"] == [20, 30, 4]


def test_superseded_running_result_never_replaces_latest_state(runtime, monkeypatch):
    engine, design_id = runtime
    entered, release = threading.Event(), threading.Event()
    real_execute = engine._execute

    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=10)
        return real_execute(*args, **kwargs)

    monkeypatch.setattr(engine, "_execute", delayed)
    old = engine.refresh(design_id)
    assert entered.wait(timeout=10)
    source = "import cadquery as cq\ndef build(p):\n    return cq.Workplane('XY').box(8,9,10)\n"
    atomic_write(engine.store.path(design_id) / "model.py", source)
    newer = engine.refresh(design_id)
    assert newer["fingerprint"] != old["fingerprint"]
    release.set()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        state = engine.refresh(design_id)
        if state["status"] != "building":
            break
        time.sleep(0.05)
    assert state["fingerprint"] == newer["fingerprint"]
    assert state["status"] == "ready", state["error"]
    assert state["result"]["stats"]["bounds"] == [8, 9, 10]
