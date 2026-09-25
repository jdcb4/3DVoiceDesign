"""Transactions and collaboration contracts against an isolated local workspace."""

import asyncio
import io
import json
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from voicedesign.server import create_app


def test_event_reconnect_after_server_restart_receives_new_changes(client):
    service = client.app.state.collaboration
    endpoint = next(route.endpoint for route in client.app.routes if route.path == "/api/events")

    class ReconnectingRequest:
        def __init__(self):
            self.headers = {"last-event-id": "100"}

        async def is_disconnected(self):
            return False

    async def check():
        response = await endpoint(ReconnectingRequest())
        stream = response.body_iterator
        assert await anext(stream) == ": connected\n\n"
        service.publish("model", "new-revision", kind="selection")
        event = await asyncio.wait_for(anext(stream), timeout=1)
        assert "id: 1" in event and '"kind": "selection"' in event
        await stream.aclose()

    asyncio.run(check())


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "designs", tmp_path / "cache")) as client:
        yield client


def blank(client):
    response = client.post("/api/designs", json={"name": "API contract", "template": None})
    assert response.status_code == 201
    design_id = response.json()["id"]
    graph = client.get(f"/api/designs/{design_id}/features").json()
    assert graph["mode"] == "structured"
    assert graph["features"] == []
    return design_id, graph["revision"]


def box_operations():
    return [
        {
            "op": "add",
            "feature": {
                "id": "profile",
                "type": "profile",
                "params": {"shape": "rectangle", "width": 20, "height": 16},
            },
        },
        {
            "op": "add",
            "feature": {
                "id": "body",
                "type": "extrude",
                "params": {"profile": "profile", "distance": 10},
            },
        },
    ]


def post_batch(client, design_id, revision, operations, **options):
    return client.post(
        f"/api/designs/{design_id}/operations",
        json={
            "expected_revision": revision,
            "operations": operations,
            **options,
        },
    )


def built_box(client):
    design_id, revision = blank(client)
    response = post_batch(client, design_id, revision, box_operations())
    assert response.status_code == 200, response.text
    return design_id, response.json()


def graph(client, design_id):
    return client.get(f"/api/designs/{design_id}/features").json()


def history(client, design_id):
    return client.get(f"/api/designs/{design_id}/history").json()


def test_multi_operation_batch_builds_and_checkpoints_once(client, monkeypatch):
    design_id, revision = blank(client)
    engine, store = client.app.state.engine, client.app.state.store
    execution, checkpoint = engine._execute, store.checkpoint
    calls = {"build": 0, "checkpoint": 0}

    def counted_build(*args, **kwargs):
        calls["build"] += 1
        return execution(*args, **kwargs)

    def counted_checkpoint(*args, **kwargs):
        calls["checkpoint"] += 1
        return checkpoint(*args, **kwargs)

    monkeypatch.setattr(engine, "_execute", counted_build)
    monkeypatch.setattr(store, "checkpoint", counted_checkpoint)
    response = post_batch(
        client, design_id, revision, box_operations(), label="Create the first block", actor="agent"
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["ok"] and result["status"] == "ready" and result["changed"]
    assert result["revision"] != revision
    assert result["body_count"] == 1
    assert result["bounds"] == [20, 16, 10]
    assert result["volume"] == pytest.approx(3200)
    assert result["feature_errors"] == []
    assert calls == {"build": 1, "checkpoint": 1}
    assert len(history(client, design_id)) == 2
    saved = graph(client, design_id)
    assert saved["revision"] == result["revision"]
    assert [f["id"] for f in saved["features"]] == ["profile", "body"]
    current = client.get(f"/api/designs/{design_id}").json()
    assert current["status"] == "ready" and current["fingerprint"] == result["revision"]
    assert calls["build"] == 1  # Reading the verified result does not enqueue a duplicate build.


def test_geometry_failure_keeps_document_history_and_current_artifact(client):
    design_id, ready = built_box(client)
    revision = ready["revision"]
    saved = graph(client, design_id)
    checkpoints = history(client, design_id)
    engine = client.app.state.engine
    directory = engine.states[design_id]["directory"]
    artifact_before = client.get(
        f"/api/designs/{design_id}/export/stl", params={"fingerprint": revision}
    ).content
    response = post_batch(
        client,
        design_id,
        revision,
        [
            {
                "op": "add",
                "feature": {
                    "id": "impossible",
                    "type": "fillet",
                    "params": {"target": "body", "radius": 99},
                },
            },
        ],
        trace_id="bad_geometry",
    )
    assert response.status_code == 422, response.text
    rejected = response.json()
    assert rejected["ok"] is False and rejected["revision"] == revision
    assert rejected["feature_errors"][0]["feature_id"] == "impossible"
    assert graph(client, design_id) == saved
    assert history(client, design_id) == checkpoints
    assert engine.states[design_id]["directory"] == directory
    artifact_after = client.get(
        f"/api/designs/{design_id}/export/stl", params={"fingerprint": revision}
    )
    assert artifact_after.status_code == 200
    assert artifact_after.content == artifact_before
    assert client.get("/api/traces/bad_geometry").json()["status"] == "rejected"


def test_stale_revision_rejected_before_build_or_history(client, monkeypatch):
    design_id, ready = built_box(client)
    checkpoints = history(client, design_id)

    def should_not_build(*args, **kwargs):
        pytest.fail("A known-stale request must not build a candidate.")

    monkeypatch.setattr(client.app.state.engine, "build_candidate", should_not_build)
    response = post_batch(
        client,
        design_id,
        "stale",
        [
            {"op": "update", "id": "body", "changes": {"params": {"distance": 40}}},
        ],
        trace_id="stale_edit",
    )
    assert response.status_code == 409
    assert graph(client, design_id)["revision"] == ready["revision"]
    assert history(client, design_id) == checkpoints
    assert client.get("/api/traces/stale_edit").json()["status"] == "conflict"


def test_edit_during_candidate_build_cannot_overwrite_newer_human_document(client, monkeypatch):
    design_id, ready = built_box(client)
    engine, store = client.app.state.engine, client.app.state.store
    reached_build, continue_build = threading.Event(), threading.Event()
    original_execute = engine._execute

    def paused_execute(*args, **kwargs):
        reached_build.set()
        assert continue_build.wait(15), "Test failed to release paused CAD build."
        return original_execute(*args, **kwargs)

    monkeypatch.setattr(engine, "_execute", paused_execute)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            post_batch,
            client,
            design_id,
            ready["revision"],
            [
                {"op": "update", "id": "body", "changes": {"params": {"distance": 30}}},
            ],
            actor="agent",
            trace_id="racing_agent",
        )
        try:
            assert reached_build.wait(15), "Candidate build did not start."
            newer, _, revision = store.read(design_id)
            newer.description = "Human edit while agent geometry was building"
            human_revision = store.commit_document(design_id, newer, revision, "Human edit")
        finally:
            continue_build.set()
        response = pending.result(timeout=15)
    assert response.status_code == 409, response.text
    saved, _, final_revision = store.read(design_id)
    assert final_revision == human_revision
    assert saved.description == "Human edit while agent geometry was building"
    assert next(f for f in saved.features if f["id"] == "body")["params"]["distance"] == 10
    assert len(history(client, design_id)) == 3  # Creation, first batch, and the human edit only.
    assert client.get("/api/traces/racing_agent").json()["status"] == "conflict"
    assert engine.states[design_id]["fingerprint"] == ready["revision"]


def test_parameters_bind_to_features_and_both_edit_paths_are_verified(client):
    design_id, revision = blank(client)
    operations = [
        {
            "op": "define_parameter",
            "name": "height",
            "parameter": {
                "label": "Height",
                "value": 10,
                "min": 1,
                "max": 100,
                "step": 1,
            },
        }
    ] + box_operations()
    operations[-1]["feature"]["params"]["distance"] = {"parameter": "height"}
    first = post_batch(client, design_id, revision, operations)
    assert first.status_code == 200, first.text
    assert first.json()["bounds"] == [20, 16, 10]
    second = post_batch(
        client,
        design_id,
        first.json()["revision"],
        [
            {"op": "set_parameters", "values": {"height": 25}},
            {"op": "update", "id": "profile", "changes": {"params": {"width": 32}}},
        ],
    )
    assert second.status_code == 200, second.text
    assert second.json()["bounds"] == [32, 16, 25]
    assert graph(client, design_id)["features"][1]["params"]["distance"] == {"parameter": "height"}
    third = client.put(
        f"/api/designs/{design_id}/parameters",
        json={
            "fingerprint": second.json()["revision"],
            "values": {"height": 15},
        },
    )
    assert third.status_code == 200, third.text
    assert third.json()["status"] == "ready"
    assert third.json()["result"]["stats"]["bounds"] == [32, 16, 15]
    checkpoints = history(client, design_id)
    invalid = client.put(
        f"/api/designs/{design_id}/parameters",
        json={
            "fingerprint": third.json()["fingerprint"],
            "values": {"height": 101},
        },
    )
    assert invalid.status_code == 422
    assert history(client, design_id) == checkpoints
    assert graph(client, design_id)["parameters"]["height"]["value"] == 15


def test_dependencies_rejected_and_replacing_shape_params_is_explicit(client):
    design_id, ready = built_box(client)
    checkpoints = history(client, design_id)
    removed = post_batch(client, design_id, ready["revision"], [{"op": "remove", "id": "profile"}])
    assert removed.status_code == 422
    assert removed.json()["feature_errors"][0]["feature_id"] == "body"
    assert graph(client, design_id)["revision"] == ready["revision"]
    assert history(client, design_id) == checkpoints
    replaced = post_batch(
        client,
        design_id,
        ready["revision"],
        [
            {
                "op": "update",
                "id": "profile",
                "changes": {
                    "replace_params": True,
                    "params": {"shape": "circle", "radius": 6},
                },
            }
        ],
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["bounds"] == [12, 12, 10]
    assert "width" not in graph(client, design_id)["features"][0]["params"]


def test_feature_selection_is_shared_and_invalidated_by_revision(client):
    design_id, ready = built_box(client)
    selected = client.put(
        f"/api/designs/{design_id}/selection",
        json={
            "expected_revision": ready["revision"],
            "feature_id": "body",
            "point": [0, 0, 10],
            "normal": [0, 0, 1],
            "actor": "human",
        },
    )
    assert selected.status_code == 200
    assert graph(client, design_id)["selection"] == selected.json()
    context = client.get(f"/api/designs/{design_id}/agent-context").json()
    assert context["selection"]["feature_id"] == "body"
    assert context["selection"]["actor"] == "human"
    updated = post_batch(
        client,
        design_id,
        ready["revision"],
        [
            {"op": "update", "id": "body", "changes": {"params": {"distance": 15}}},
        ],
    ).json()
    assert graph(client, design_id)["selection"]["feature_id"] is None
    stale = client.put(
        f"/api/designs/{design_id}/selection",
        json={
            "expected_revision": ready["revision"],
            "feature_id": "body",
            "actor": "agent",
        },
    )
    assert stale.status_code == 409
    unknown = client.put(
        f"/api/designs/{design_id}/selection",
        json={
            "expected_revision": updated["revision"],
            "feature_id": "unknown",
            "actor": "agent",
        },
    )
    assert unknown.status_code == 422


def test_custom_source_escape_executes_python_and_preserves_original_graph_archive(client):
    design_id, ready = built_box(client)
    archive = client.get(f"/api/designs/{design_id}/export/source")
    with zipfile.ZipFile(io.BytesIO(archive.content)) as files:
        saved = json.loads(files.read(f"{design_id}/design.json"))
        assert [f["id"] for f in saved["features"]] == ["profile", "body"]
        assert any("/history/" in name for name in files.namelist())
        assert "return Model([])" in files.read(f"{design_id}/model.py").decode()
    source_path = client.app.state.store.path(design_id) / "model.py"
    source_path.write_text(
        "import cadquery as cq\n\ndef build(p):\n"
        "    return cq.Workplane().box(3, 4, p.get('height', 5))\n",
        encoding="utf-8",
    )
    python_graph = graph(client, design_id)
    assert python_graph["mode"] == "python" and python_graph["features"] is None
    assert python_graph["revision"] != ready["revision"]
    # Effective mode is Python, but the last saved graph must remain recoverable.
    escaped_archive = client.get(f"/api/designs/{design_id}/export/source")
    with zipfile.ZipFile(io.BytesIO(escaped_archive.content)) as files:
        saved = json.loads(files.read(f"{design_id}/design.json"))
        assert [f["id"] for f in saved["features"]] == ["profile", "body"]
        assert "p.get('height', 5)" in files.read(f"{design_id}/model.py").decode()
    manual_checkpoint = client.post(
        f"/api/designs/{design_id}/history",
        json={
            "label": "Keep graph when entering source mode",
        },
    )
    assert manual_checkpoint.status_code == 201
    history_path = source_path.parent / "history"
    preserved = json.loads((history_path / (manual_checkpoint.json()["id"] + ".json")).read_text())
    assert [f["id"] for f in preserved["design"]["features"]] == ["profile", "body"]
    assert "p.get('height', 5)" in preserved["source"]
    history_before_commit = {item["id"] for item in history(client, design_id)}
    rejected = post_batch(client, design_id, python_graph["revision"], box_operations())
    assert rejected.status_code == 422
    assert "custom Python" in rejected.json()["detail"]
    custom = post_batch(
        client,
        design_id,
        python_graph["revision"],
        [
            {
                "op": "define_parameter",
                "name": "height",
                "parameter": {
                    "label": "Height",
                    "value": 9,
                    "min": 1,
                    "max": 20,
                },
            }
        ],
    )
    assert custom.status_code == 200, custom.text
    assert custom.json()["bounds"] == [3, 4, 9]
    assert custom.json()["timings"]["worker_mode"] == "isolated"
    assert graph(client, design_id)["mode"] == "python"
    added_checkpoints = {item["id"] for item in history(client, design_id)} - history_before_commit
    assert len(added_checkpoints) == 1
    before_parameter_commit = json.loads(
        (history_path / (added_checkpoints.pop() + ".json")).read_text()
    )
    assert [f["id"] for f in before_parameter_commit["design"]["features"]] == ["profile", "body"]
    # Once the Python-mode parameter commit clears the active graph, the full
    # source archive still contains those retained graphs in portable history.
    final_archive = client.get(f"/api/designs/{design_id}/export/source")
    with zipfile.ZipFile(io.BytesIO(final_archive.content)) as files:
        checkpoints = [
            json.loads(files.read(name)) for name in files.namelist() if "/history/" in name
        ]
        retained_graphs = [
            item["design"]["features"] for item in checkpoints if item["design"]["features"]
        ]
        assert len(retained_graphs) == 2
        assert all(
            [f["id"] for f in definitions] == ["profile", "body"] for definitions in retained_graphs
        )


def test_trace_reports_actor_real_stages_and_first_viewer_completion(client):
    design_id, revision = blank(client)
    response = post_batch(
        client,
        design_id,
        revision,
        box_operations(),
        actor="human",
        trace_id="timed_human",
        agent_timing={"planning_ms": 123, "tool_dispatch_ms": 45},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    trace = client.get("/api/traces/timed_human").json()
    assert trace["actor"] == "human" and trace["status"] == "committed"
    assert trace["revision"] == result["revision"]
    assert trace["operation_count"] == 2
    assert trace["agent_timing"] == {"planning_ms": 123, "tool_dispatch_ms": 45}
    assert trace["agent_timing_source"] == "caller_reported"
    assert trace["viewer"] is None and trace["client"] is None
    for stage in (
        "api_seconds",
        "kernel_seconds",
        "validation_seconds",
        "mesh_seconds",
        "export_seconds",
        "queue_seconds",
        "commit_seconds",
        "prepare_seconds",
    ):
        assert trace["timings"][stage] >= 0
    assert client.get(f"/api/designs/{design_id}").json()["trace_id"] == "timed_human"
    assert (
        client.post(
            "/api/traces/timed_human/viewer",
            json={
                "revision": "wrong",
                "load_ms": 5,
                "render_ms": 10,
            },
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/traces/timed_human/viewer",
            json={
                "revision": result["revision"],
                "load_ms": 5,
                "render_ms": 10,
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/traces/timed_human/viewer",
            json={
                "revision": result["revision"],
                "load_ms": 999,
                "render_ms": 999,
            },
        ).status_code
        == 200
    )
    assert client.post("/api/traces/timed_human/client", json={"http_ms": 75}).status_code == 200
    observed = client.get("/api/traces/timed_human").json()
    assert observed["viewer"]["load_ms"] == 5 and observed["viewer"]["render_ms"] == 10
    assert observed["viewer"]["source"] == "browser_reported"
    assert observed["client"]["http_ms"] == 75
    duplicate = post_batch(
        client,
        design_id,
        result["revision"],
        [
            {"op": "update", "id": "body", "changes": {"name": "New name"}},
        ],
        trace_id="timed_human",
    )
    assert duplicate.status_code == 409
    assert client.get("/api/traces/timed_human").json() == observed


def test_capabilities_and_schema_are_discoverable_without_starting_worker(client, monkeypatch):
    def no_build(*args, **kwargs):
        pytest.fail("Schema discovery must not invoke a model build.")

    engine = client.app.state.engine
    monkeypatch.setattr(engine, "build_candidate", no_build)
    monkeypatch.setattr(engine, "refresh", no_build)
    capabilities = client.get("/api/capabilities")
    assert capabilities.status_code == 200
    all_specs = capabilities.json()
    assert all_specs["units"] == "mm"
    assert set(all_specs["operations"]) == {"profile", "extrude", "fillet", "shell", "boolean"}
    focused = client.get("/api/capabilities", params={"operation": "extrude"}).json()
    assert list(focused["operations"]) == ["extrude"]
    assert len(json.dumps(focused)) < len(json.dumps(all_specs))
    schema = client.get(all_specs["batch_schema_url"]).json()
    assert {"expected_revision", "operations"} <= set(schema["required"])
    assert schema["properties"]["operations"]["minItems"] == 1
    assert "discriminator" in schema["properties"]["operations"]["items"]
    assert client.get("/api/capabilities", params={"operation": "unknown"}).status_code == 422
    assert engine.worker.process is None
    design_id, revision = blank(client)
    for invalid_trace_id in ("-trace", "_trace"):
        rejected = post_batch(
            client, design_id, revision, box_operations(), trace_id=invalid_trace_id
        )
        assert rejected.status_code == 422
        assert rejected.json()["detail"][0]["loc"] == ["body", "trace_id"]
    assert len(history(client, design_id)) == 1
    assert engine.worker.process is None


def test_event_broker_bounds_history_and_does_not_attach_old_trace_to_new_revision(client):
    service = client.app.state.collaboration
    for number in range(260):
        service.publish(
            "test-model", f"revision-{number}", actor="agent", trace_id=f"trace-{number}"
        )
    assert service.sequence == 260 and len(service.events) == 256
    assert service.events[0][0] == 5 and service.events[-1][0] == 260
    current = service.decorate({"id": "test-model", "fingerprint": "revision-259"})
    assert current["trace_id"] == "trace-259"
    stale = service.decorate({"id": "test-model", "fingerprint": "changed-outside-api"})
    assert stale["trace_id"] is None
    service.publish("test-model", "revision-259", actor="human", kind="selection")
    assert service.latest["test-model"]["trace_id"] == "trace-259"
    assert service.events[-1][1]["kind"] == "selection"
    assert client.get("/api/events", headers={"last-event-id": "not-an-integer"}).status_code == 422
