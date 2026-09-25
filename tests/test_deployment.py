"""Storage, installation boundaries, public contracts, and lifecycle invariants."""

import json
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from voicedesign import API_VERSION, __version__
from voicedesign.api_models import BatchError, ErrorResponse
from voicedesign.config import PACKAGE, Workspace, resolve_workspace
from voicedesign.launcher import exclusive_lock, main, process_identity, recorded_process, stop
from voicedesign.server import create_app


def test_workspace_precedence_and_relative_project_pointer(tmp_path, monkeypatch):
    app_home = tmp_path / "app"
    app_home.mkdir()
    monkeypatch.setenv("VOICEDESIGN_HOME", str(app_home))
    monkeypatch.delenv("VOICEDESIGN_WORKSPACE", raising=False)
    project = tmp_path / "project"
    nested = project / "src"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert resolve_workspace().root == app_home / "workspaces/default"
    (app_home / "config.toml").write_text('default_workspace = "library/default"')
    assert resolve_workspace().root == app_home / "library/default"
    monkeypatch.setenv("VOICEDESIGN_WORKSPACE", str(tmp_path / "environment"))
    assert resolve_workspace().root == tmp_path / "environment"
    (project / ".voicedesign.toml").write_text('workspace = "cad"')
    assert resolve_workspace().root == project / "cad"
    assert resolve_workspace(tmp_path / "explicit").root == tmp_path / "explicit"
    with pytest.raises(ValueError, match="outside"):
        resolve_workspace(PACKAGE / "data")
    assert Workspace(project / "cad").runtime != Workspace(tmp_path / "other").runtime


def test_init_refuses_to_replace_project_configuration(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    main(["init"])
    assert json.loads(capsys.readouterr().out)["workspace_path"] == str(tmp_path / "cad")
    original = (tmp_path / ".voicedesign.toml").read_bytes()
    with pytest.raises(SystemExit) as error:
        main(["--workspace", str(tmp_path / "different"), "init"])
    assert error.value.code == 1
    assert (tmp_path / ".voicedesign.toml").read_bytes() == original


def test_workspace_lock_excludes_another_process_and_releases_after_exit(tmp_path):
    path = tmp_path / ".voicedesign.lock"
    code = (
        "from pathlib import Path; from voicedesign.launcher import exclusive_lock; "
        "import sys; lock=exclusive_lock(Path(sys.argv[1])); lock.__enter__(); lock.__exit__(None,None,None)"
    )
    with exclusive_lock(path):
        result = subprocess.run(
            [sys.executable, "-c", code, str(path)], check=False, capture_output=True
        )
        assert result.returncode != 0 and b"already in use" in result.stderr
    assert (
        subprocess.run(
            [sys.executable, "-c", code, str(path)], check=False, capture_output=True
        ).returncode
        == 0
    )


def test_stop_does_not_send_to_an_unverified_process(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEDESIGN_HOME", str(tmp_path / "home"))
    workspace = Workspace(tmp_path / "workspace")
    workspace.runtime.mkdir(parents=True)
    (workspace.runtime / "server.json").write_text(
        json.dumps({"port": 9999, "instance_id": "old", "token": "private"})
    )
    monkeypatch.setattr("voicedesign.launcher.health", lambda port: {"app": "another-service"})
    assert stop(workspace)["status"] == "stopped"


def test_process_identity_rejects_recycled_pid():
    import os

    identity = process_identity(os.getpid())
    assert recorded_process(identity).pid == os.getpid()
    assert recorded_process({**identity, "created": identity["created"] - 1}) is None
    assert recorded_process({}) is None


def test_stop_waits_for_process_exit_after_record_removal(tmp_path, monkeypatch):
    import io

    monkeypatch.setenv("VOICEDESIGN_HOME", str(tmp_path / "home"))
    workspace = Workspace(tmp_path / "workspace")
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
    try:
        record = {
            "instance_id": "verified",
            "token": "local",
            "process": process_identity(process.pid),
        }
        records = iter([record, None])
        monkeypatch.setattr("voicedesign.launcher.running", lambda _: {"url": "http://local"})
        monkeypatch.setattr("voicedesign.launcher.read_record", lambda _: next(records))
        monkeypatch.setattr("voicedesign.launcher.urlopen", lambda *a, **kw: io.StringIO("{}"))
        assert process.poll() is None
        assert stop(workspace)["status"] == "stopped"
        assert process.poll() == 0
    finally:
        process.wait(timeout=5)


def test_public_contract_and_offline_docs(tmp_path):
    with TestClient(create_app(tmp_path / "designs", tmp_path / "cache")) as client:
        health = client.get("/api/health").json()
        assert health["version"] == __version__ and health["api_version"] == API_VERSION
        assert health["workspace_path"] == str(tmp_path)
        assert client.get("/docs").status_code == 200
        assert "cdn" not in client.get("/docs").text.lower()
        schema = client.get("/openapi.json").json()
        assert schema["info"]["version"] == __version__
        for path, methods in schema["paths"].items():
            for method, operation in methods.items():
                if method not in {"get", "post", "put"}:
                    continue
                for code, response in operation["responses"].items():
                    if code.startswith("2"):
                        assert response.get("content"), (path, code)
                        for media in response["content"].values():
                            assert media.get("schema"), (path, code)
        for response in [
            client.post("/api/designs", json={}),
            client.get("/api/designs/missing/features"),
            client.get("/api/health", headers={"host": "bad.example"}),
            client.post("/api/runtime/stop", json={}),
        ]:
            assert response.status_code >= 400
            ErrorResponse.model_validate(response.json())
        design = client.post("/api/designs", json={"name": "Errors", "template": None}).json()["id"]
        revision = client.get(f"/api/designs/{design}/features").json()["revision"]
        rejected = client.post(
            f"/api/designs/{design}/operations",
            json={"expected_revision": revision, "operations": [{"op": "remove", "id": "absent"}]},
        )
        assert rejected.status_code == 422
        BatchError.model_validate(rejected.json())
        assert client.get(f"/api/designs/{design}/features").json()["revision"] == revision
