import json
import sys

import pytest
from fastapi.testclient import TestClient

from voicedesign.client import Client, main
from voicedesign.server import create_app


def test_one_command_edit_waits_for_real_geometry_and_reports_build_failures(
    tmp_path, monkeypatch, capsys
):
    with TestClient(create_app(tmp_path / "designs", tmp_path / "cache")) as api:
        client = Client()

        def request(path, method="GET", body=None, binary=False):
            response = api.request(method, "/api" + path, json=body)
            response.raise_for_status()
            return response.content if binary else response.json()

        monkeypatch.setattr(client, "request", request)
        design_id = client.new("Voice edit")["id"]
        monkeypatch.setattr("voicedesign.client.Client", lambda port: client)
        monkeypatch.setattr(
            sys, "argv", ["client", "set", design_id, "width=100", "--wait", "--brief"]
        )
        main()
        result = json.loads(capsys.readouterr().out)
        assert result["status"] == "ready"
        assert result["parameters"]["width"] == 100
        assert result["stats"]["bounds"] == [100, 50, 6]
        assert result["build_seconds"] > 0
        assert result["command_seconds"] > 0
        assert client.request(client.design_path(design_id) + "/history")

        # A saved but invalid model must not be reported as a successful voice edit.
        (api.app.state.store.path(design_id) / "model.py").write_text(
            "def build(p):\n    raise ValueError('Invalid voice edit')\n"
        )
        with pytest.raises(RuntimeError, match="Invalid voice edit"):
            client.status(design_id, wait=True)


def test_wait_rejects_a_revision_changed_by_another_editor(monkeypatch):
    client = Client()
    monkeypatch.setattr("voicedesign.client.time.sleep", lambda seconds: None)
    monkeypatch.setattr(
        client, "request", lambda path: {"status": "ready", "fingerprint": "another-edit"}
    )
    with pytest.raises(RuntimeError, match="changed during"):
        client.wait_for_build("part", {"status": "building", "fingerprint": "my-edit"})


def test_wait_is_bounded(monkeypatch):
    ticks = iter([0, 111])
    monkeypatch.setattr("voicedesign.client.time.monotonic", lambda: next(ticks))
    with pytest.raises(TimeoutError, match="still building"):
        Client().wait_for_build("part", {"status": "building", "fingerprint": "slow-edit"})
