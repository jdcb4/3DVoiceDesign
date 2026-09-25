import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from voicedesign.server import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "designs", tmp_path / "cache")) as client:
        yield client


def test_create_edit_checkpoint_and_source_archive(client):
    response = client.post("/api/designs", json={"name": "My plate"})
    assert response.status_code == 201
    design_id = response.json()["id"]
    state = client.get(f"/api/designs/{design_id}").json()
    changed = client.put(
        f"/api/designs/{design_id}/parameters",
        json={"values": {"width": 100}, "fingerprint": state["fingerprint"]},
    )
    assert changed.status_code == 200
    assert changed.json()["design"]["parameters"]["width"]["value"] == 100
    stale = client.put(
        f"/api/designs/{design_id}/parameters",
        json={"values": {"width": 150}, "fingerprint": state["fingerprint"]},
    )
    assert stale.status_code == 409
    archive = client.get(f"/api/designs/{design_id}/export/source")
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as files:
        assert f"{design_id}/model.py" in files.namelist()
        assert f"{design_id}/design.json" in files.namelist()
        assert any("history/" in name for name in files.namelist())


def test_cross_origin_writes_and_unknown_templates_are_rejected(client):
    response = client.post(
        "/api/designs", json={"name": "Unwanted"}, headers={"origin": "https://example.org"}
    )
    assert response.status_code == 403
    assert client.get("/api/designs").json() == []
    assert (
        client.post("/api/designs", json={"name": "Test", "template": "../../x"}).status_code == 422
    )
    assert client.get("/api/health", headers={"host": "untrusted.example"}).status_code == 400


def test_invalid_manifest_is_reported_without_server_crash(client):
    design_id = client.post("/api/designs", json={"name": "Broken"}).json()["id"]
    (client.app.state.store.path(design_id) / "design.json").write_text('{"name":')
    assert client.get(f"/api/designs/{design_id}").status_code == 422
    assert client.get("/api/health").status_code == 200


def test_active_design_is_validated_and_persists_across_server_instances(client):
    assert client.get("/api/workspace").json()["active_design"] is None
    design_id = client.post("/api/designs", json={"name": "Visible part"}).json()["id"]
    selected = client.put("/api/workspace/active", json={"design_id": design_id})
    assert selected.status_code == 200
    assert selected.json() == {"active_design": design_id, "revision": 1}
    assert client.put("/api/workspace/active", json={"design_id": "missing"}).status_code == 404
    assert client.put("/api/workspace/active", json={"design_id": "../escape"}).status_code == 422
    assert client.get("/api/workspace").json()["active_design"] == design_id
    with TestClient(
        create_app(client.app.state.store.root, client.app.state.engine.cache)
    ) as reopened:
        assert reopened.get("/api/workspace").json()["active_design"] == design_id
        # Selecting the same design is idempotent.
        assert (
            reopened.put("/api/workspace/active", json={"design_id": design_id}).json()["revision"]
            == 1
        )


def test_blank_creation_source_archive_and_agent_context(client):
    response = client.post("/api/designs", json={"name": "Empty canvas", "template": None})
    assert response.status_code == 201
    design_id = response.json()["id"]
    source = client.get(f"/api/designs/{design_id}/source").json()
    assert source["design"]["parameters"] == {}
    assert "return Model([])" in source["source"]
    assert len(client.get(f"/api/designs/{design_id}/history").json()) == 1
    archive = client.get(f"/api/designs/{design_id}/export/source")
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as files:
        assert "Model([])" in files.read(f"{design_id}/model.py").decode()
    context = client.get(
        f"/api/designs/{design_id}/agent-context", headers={"host": "127.0.0.1:9876"}
    ).json()
    assert context["design_id"] == design_id
    assert context["design_url"] == f"http://127.0.0.1:9876/api/designs/{design_id}"
    assert context["source_path"] == str(client.app.state.store.path(design_id) / "model.py")
    assert json.dumps(context["manifest_path"]) in context["instructions"]
    assert "fingerprint" in context["instructions"]
    assert "There is no source-upload" in context["instructions"]
    assert client.get("/api/designs/missing/agent-context").status_code == 404
