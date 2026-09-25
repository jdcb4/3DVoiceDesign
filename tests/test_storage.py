import json

import pytest

from voicedesign.engine import ROOT
from voicedesign.storage import Conflict, Store, atomic_write


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path / "designs")
    result.create("Test plate", ROOT / "templates" / "mounting-plate")
    return result


def test_checkpoint_restores_source_and_parameters_and_preserves_current(store):
    initial, code, fingerprint = store.read("test-plate")
    revision = store.checkpoint("test-plate", "Original")
    store.update_parameters("test-plate", {"width": 110}, fingerprint)
    atomic_write(store.path("test-plate") / "model.py", code + "\n# edited geometry\n")
    _, _, modified = store.read("test-plate")
    store.restore("test-plate", revision["id"], modified)
    restored, restored_code, _ = store.read("test-plate")
    assert restored == initial
    assert restored_code == code
    backups = [
        r for r in store.history("test-plate") if r["label"] == "Before restoring checkpoint"
    ]
    assert len(backups) == 1
    backup = json.loads(
        (store.path("test-plate") / "history" / (backups[0]["id"] + ".json")).read_text()
    )
    assert backup["design"]["parameters"]["width"]["value"] == 110
    assert "edited geometry" in backup["source"]


def test_conflicting_edits_do_not_overwrite_source_changes(store):
    _, source, fingerprint = store.read("test-plate")
    atomic_write(store.path("test-plate") / "model.py", source + "\n# newer edit\n")
    with pytest.raises(Conflict):
        store.update_parameters("test-plate", {"width": 120}, fingerprint)
    assert store.read("test-plate")[0].parameters["width"].value == 80


@pytest.mark.parametrize(
    "values", [{"width": -5}, {"width": float("nan")}, {"bad": 2}, {"width": True}]
)
def test_invalid_values_are_not_saved(store, values):
    before = store.read("test-plate")
    with pytest.raises(ValueError):
        store.update_parameters("test-plate", values, before[2])
    assert store.read("test-plate") == before


@pytest.mark.parametrize("design_id", ["../escape", "..", "C:\\Windows", "x/y", "x\\y"])
def test_paths_cannot_escape_designs(store, design_id):
    with pytest.raises(ValueError):
        store.path(design_id)


def test_new_designs_have_independent_sources_and_histories(store):
    copy_id = store.create("Test plate", store.path("test-plate"))
    assert copy_id != "test-plate"
    assert len(store.history(copy_id)) == 1
    _, _, fingerprint = store.read(copy_id)
    store.update_parameters(copy_id, {"width": 130}, fingerprint)
    assert store.read("test-plate")[0].parameters["width"].value == 80
