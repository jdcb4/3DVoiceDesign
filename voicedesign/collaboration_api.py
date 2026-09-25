"""Shared feature transactions, selection, revision events and local latency traces."""

import asyncio
import json
import threading
import time
import uuid
from collections import deque
from datetime import UTC, datetime

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import Field

from voicedesign import API_VERSION
from voicedesign import api_models as api
from voicedesign.operations import BatchRequest, StrictModel, apply_operations
from voicedesign.storage import ID, Conflict, atomic_write, document_text, revision_for


class SelectionRequest(StrictModel):
    expected_revision: str
    feature_id: str | None = None
    point: list[float] | None = Field(default=None, min_length=3, max_length=3)
    normal: list[float] | None = Field(default=None, min_length=3, max_length=3)
    actor: str = Field(default="human", pattern="^(human|agent)$")


class ViewerTiming(StrictModel):
    revision: str
    load_ms: float = Field(ge=0, le=86_400_000)
    render_ms: float = Field(ge=0, le=86_400_000)


class ClientTiming(StrictModel):
    http_ms: float = Field(ge=0, le=86_400_000)
    planning_ms: float | None = Field(default=None, ge=0, le=86_400_000)
    tool_dispatch_ms: float | None = Field(default=None, ge=0, le=86_400_000)


class Collaboration:
    def __init__(self, store, engine):
        self.store, self.engine = store, engine
        self.root = engine.cache / "collaboration"
        self.lock = threading.RLock()
        self.events = deque(maxlen=256)
        self.sequence = 0
        self.latest = {}

    def publish(self, design_id, revision, *, actor=None, trace_id=None, kind="model"):
        with self.lock:
            self.sequence += 1
            event = {
                "design_id": design_id,
                "revision": revision,
                "actor": actor,
                "trace_id": trace_id,
                "kind": kind,
            }
            self.events.append((self.sequence, event))
            if trace_id:
                self.latest[design_id] = {
                    "revision": revision,
                    "trace_id": trace_id,
                    "actor": actor,
                }

    def decorate(self, state):
        with self.lock:
            last = self.latest.get(state["id"], {})
            return {
                **state,
                "trace_id": last.get("trace_id")
                if last.get("revision") == state["fingerprint"]
                else None,
            }

    def trace_path(self, trace_id):
        if not ID.fullmatch(trace_id):
            raise ValueError("Invalid trace identifier.")
        return self.root / "traces" / (trace_id + ".json")

    def trace(self, trace_id):
        with self.lock:
            return json.loads(self.trace_path(trace_id).read_text(encoding="utf-8"))

    def write_trace(self, trace):
        with self.lock:
            atomic_write(self.trace_path(trace["trace_id"]), json.dumps(trace, indent=2) + "\n")

    def selection(self, design_id):
        _, _, revision = self.store.read(design_id)
        path = self.root / "selection" / (design_id + ".json")
        with self.lock:
            data = json.loads(path.read_text()) if path.exists() else {}
        if data.get("revision") != revision:
            return {
                "revision": revision,
                "feature_id": None,
                "point": None,
                "normal": None,
                "actor": None,
            }
        return data

    def graph(self, design_id):
        with self.store.lock:
            doc, _, revision = self.store.read(design_id)
            return {
                "revision": revision,
                "mode": "structured" if doc.features is not None else "python",
                "features": doc.features,
                "parameters": {k: p.model_dump() for k, p in doc.parameters.items()},
                "selection": self.selection(design_id),
            }

    def batch(self, design_id, body: BatchRequest):
        started = time.perf_counter()
        trace_id = body.trace_id or uuid.uuid4().hex
        with self.lock:
            if self.trace_path(trace_id).exists():
                raise Conflict(
                    "Trace ID already used. Read that trace and the current revision before retrying."
                )
            trace = {
                "trace_id": trace_id,
                "design_id": design_id,
                "actor": body.actor,
                "received_at": datetime.now(UTC).isoformat(),
                "expected_revision": body.expected_revision,
                "operation_count": len(body.operations),
                "status": "preparing",
                "agent_timing": body.agent_timing.model_dump()
                if body.agent_timing
                else {"planning_ms": None, "tool_dispatch_ms": None},
                "agent_timing_source": "caller_reported" if body.agent_timing else "unavailable",
                "viewer": None,
                "client": None,
            }
            self.write_trace(trace)
        try:
            with self.store.lock:
                original, source, revision = self.store.read(design_id)
                if revision != body.expected_revision:
                    raise Conflict(
                        "Design changed. Read its current feature definitions and reconcile before editing."
                    )
                candidate = apply_operations(original, body.operations)
            validation_seconds = time.perf_counter() - started
            if candidate == original:
                # No persistence/checkpoint for a no-op; ensure the existing revision is verified.
                fingerprint = revision
            else:
                fingerprint = revision_for(document_text(candidate), source)
            state = self.engine.build_candidate(design_id, source, candidate, fingerprint)
            if state["status"] != "ready":
                errors = state.get("feature_errors") or [
                    {"feature_id": None, "code": "build_failed", "message": state.get("error")}
                ]
                trace.update(
                    status="rejected",
                    revision=revision,
                    feature_errors=errors,
                    timings={
                        **state.get("timings", {}),
                        "prepare_seconds": validation_seconds,
                        "api_seconds": time.perf_counter() - started,
                    },
                )
                self.write_trace(trace)
                return JSONResponse(
                    {
                        "ok": False,
                        "code": "batch_rejected",
                        "revision": revision,
                        "trace_id": trace_id,
                        "detail": state.get("error"),
                        "feature_errors": errors,
                        "timings": trace["timings"],
                    },
                    status_code=422,
                )
            commit_started = time.perf_counter()
            with self.store.lock:
                new_revision = self.store.commit_document(
                    design_id, candidate, revision, f"Before {body.actor}: {body.label}"
                )
                if new_revision != fingerprint:
                    raise RuntimeError("Candidate revision did not match the committed document.")
                self.engine.adopt(design_id, state)
            timings = {
                **state.get("timings", {}),
                "prepare_seconds": validation_seconds,
                "commit_seconds": time.perf_counter() - commit_started,
                "api_seconds": time.perf_counter() - started,
            }
            stats = state["result"].get("stats") or {}
            trace.update(
                status="committed",
                revision=new_revision,
                feature_errors=[],
                timings=timings,
                body_count=stats.get("solids", 0),
                bounds=stats.get("bounds"),
            )
            self.write_trace(trace)
            self.publish(design_id, new_revision, actor=body.actor, trace_id=trace_id)
            return {
                "ok": True,
                "id": design_id,
                "revision": new_revision,
                "trace_id": trace_id,
                "features": candidate.features,
                "feature_errors": [],
                "body_count": stats.get("solids", 0),
                "bounds": stats.get("bounds"),
                "volume": stats.get("volume", 0),
                "timings": timings,
                "status": "ready",
                "changed": candidate != original,
            }
        except (ValueError, Conflict) as error:
            trace.update(
                status="conflict" if isinstance(error, Conflict) else "rejected",
                error=str(error),
                timings={"api_seconds": time.perf_counter() - started},
            )
            if hasattr(error, "as_dict"):
                trace["feature_errors"] = [error.as_dict()]
            self.write_trace(trace)
            if isinstance(error, Conflict):
                raise
            return JSONResponse(
                {
                    "ok": False,
                    "code": "batch_rejected",
                    "revision": body.expected_revision,
                    "trace_id": trace_id,
                    "detail": str(error),
                    "feature_errors": trace.get("feature_errors", []),
                    "timings": trace["timings"],
                },
                status_code=422,
            )
        except Exception as error:
            trace.update(
                status="error",
                error=str(error),
                timings={"api_seconds": time.perf_counter() - started},
            )
            self.write_trace(trace)
            raise


def install_routes(app, store, engine):
    service = Collaboration(store, engine)
    app.state.collaboration = service

    @app.get("/api/capabilities", response_model=api.Capabilities)
    def capabilities(operation: str | None = None):
        from voicedesign.features import capabilities as specs

        result = specs()
        if operation is not None:
            if operation not in result["operations"]:
                raise ValueError(
                    "Unknown operation. Use profile, extrude, fillet, shell or boolean."
                )
            result["operations"] = {operation: result["operations"][operation]}
        return {
            **result,
            "api_version": API_VERSION,
            "batch_endpoint": "/api/designs/{id}/operations",
            "batch_schema_url": "/api/operations/schema",
            "batch_operations": [
                "add",
                "update",
                "remove",
                "move",
                "set_parameters",
                "define_parameter",
                "remove_parameter",
            ],
            "revision_policy": "Required expected_revision; build then commit; stale or failed edits are not saved.",
            "custom_code": "Local model.py remains the trusted Python escape hatch. No source-upload API.",
        }

    @app.get("/api/operations/schema", response_model=dict[str, object])
    def batch_schema():
        return BatchRequest.model_json_schema()

    @app.get("/api/designs/{design_id}/features", response_model=api.FeatureGraph)
    def graph(design_id: str):
        return service.graph(design_id)

    @app.post(
        "/api/designs/{design_id}/operations",
        response_model=api.BatchResult,
        responses={422: {"model": api.BatchError | api.ErrorResponse}},
    )
    def batch(design_id: str, body: BatchRequest):
        return service.batch(design_id, body)

    @app.get("/api/designs/{design_id}/selection", response_model=api.Selection)
    def selection(design_id: str):
        return service.selection(design_id)

    @app.put("/api/designs/{design_id}/selection", response_model=api.Selection)
    def select(design_id: str, body: SelectionRequest):
        with store.lock:
            doc, _, revision = store.read(design_id)
            if revision != body.expected_revision:
                raise Conflict("Selection belongs to an older model revision. Pick again.")
            if body.feature_id and (
                doc.features is None or body.feature_id not in {f["id"] for f in doc.features}
            ):
                raise ValueError("Select a current stable structured feature ID.")
            data = {
                "revision": revision,
                "feature_id": body.feature_id,
                "point": body.point,
                "normal": body.normal,
                "actor": body.actor,
            }
            with service.lock:
                atomic_write(service.root / "selection" / (design_id + ".json"), json.dumps(data))
        service.publish(design_id, revision, actor=body.actor, kind="selection")
        return data

    @app.get("/api/traces/{trace_id}", response_model=api.Trace)
    def trace(trace_id: str):
        return service.trace(trace_id)

    @app.post("/api/traces/{trace_id}/viewer", response_model=api.Okay)
    def viewer(trace_id: str, body: ViewerTiming):
        with service.lock:
            trace = service.trace(trace_id)
            if trace.get("revision") != body.revision or trace.get("status") != "committed":
                raise Conflict("Viewer timing does not match a committed trace revision.")
            # First completed display is latency; revisiting the model must not replace it.
            if trace.get("viewer") is None:
                trace["viewer"] = {
                    **body.model_dump(),
                    "received_at": datetime.now(UTC).isoformat(),
                    "source": "browser_reported",
                }
                service.write_trace(trace)
        return {"ok": True}

    @app.post("/api/traces/{trace_id}/client", response_model=api.Okay)
    def client(trace_id: str, body: ClientTiming):
        with service.lock:
            trace = service.trace(trace_id)
            trace["client"] = {**body.model_dump(), "source": "caller_reported"}
            service.write_trace(trace)
        return {"ok": True}

    @app.get(
        "/api/events",
        response_class=StreamingResponse,
        responses={
            200: {
                "description": "SSE change events; reconnect and reread current state",
                "content": {"text/event-stream": {"schema": {"type": "string"}}},
            }
        },
    )
    async def events(request: Request):
        try:
            cursor = int(request.headers.get("last-event-id", "0"))
        except ValueError:
            raise HTTPException(422, "Invalid event cursor.") from None
        with service.lock:
            # Event IDs restart with the process. A reconnect from the previous
            # server must not wait for its old sequence number to be reached.
            if cursor < 0 or cursor > service.sequence:
                cursor = 0

        async def stream():
            nonlocal cursor
            yield ": connected\n\n"
            heartbeat = time.monotonic()
            while not await request.is_disconnected():
                with service.lock:
                    pending = [
                        (sequence, event) for sequence, event in service.events if sequence > cursor
                    ]
                for sequence, event in pending:
                    cursor = sequence
                    yield f"id: {sequence}\nevent: change\ndata: {json.dumps(event)}\n\n"
                if time.monotonic() - heartbeat > 15:
                    yield ": heartbeat\n\n"
                    heartbeat = time.monotonic()
                await asyncio.sleep(0.1)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return service
