# API contract 1

The canonical HTTP contract is generated from the request/response models at
`/openapi.json`. `/docs` renders it locally without CDN dependencies. App version
and API version appear in `/api/health`, and API version in `/api/capabilities`.
This release keeps `/api/...` URLs for compatibility; breaking contracts will use
a new explicit API version. Additive fields may be introduced within version 1.
Clients should ignore unknown fields and verify `api_version == "1"`.

## Connect and target

Run `voicedesign start` in the configured project. Read its returned URL and
workspace path. `voicedesign api ...` verifies the managed workspace identity;
`voicedesign-client --port PORT ...` is the explicit low-level client.

Retain the design ID returned by creation. The viewer's active selection is shared
UI state, not an authoritative target for an ongoing task. Use
`GET /api/designs/{id}/agent-context` for exact source paths and operation URLs.
In Docker those filesystem paths are container-relative.

## Resources

| Resource | Purpose |
| --- | --- |
| GET `/api/health` | Application/API versions and workspace identity |
| GET `/api/workspace`, PUT `/api/workspace/active` | Workspace and shared viewer selection |
| GET/POST `/api/designs`, GET `/api/templates` | List/create designs and discover templates |
| GET `/api/designs/{id}` | Current build state, fingerprint, parameters, solid statistics |
| GET `/api/designs/{id}/features` | Structured definitions, parameters, selection, revision |
| POST `/api/designs/{id}/operations` | Atomic, revision-checked feature batch |
| PUT `/api/designs/{id}/parameters` | Named dimensions; requires fingerprint |
| GET `/api/designs/{id}/source` | Editable manifest and Python source |
| GET/POST `/api/designs/{id}/history` | Read/create checkpoints |
| POST `/api/designs/{id}/history/{revision}/restore` | Restore with current fingerprint |
| GET/PUT `/api/designs/{id}/selection` | Revision-bound feature/mesh selection |
| GET `/api/designs/{id}/mesh/{index}` | JSON preview, requires current fingerprint |
| GET `/api/designs/{id}/export/{format}` | STL/STEP binary or source ZIP |
| GET `/api/capabilities`, `/api/operations/schema` | Focused vocabulary and JSON Schema |
| GET `/api/traces/{id}` | Outcome and build timing evidence |
| POST `/api/traces/{id}/client`, `/viewer` | Optional caller/browser timing observations |
| GET `/api/events` | SSE change notifications; reread state after reconnect |

Lengths are mm, volumes mm³, timing fields ending `_seconds` are seconds; `_ms`
are milliseconds. `fingerprint` and `revision` identify the same saved source and
manifest content. Parameter bindings preserve named dimensions. See
[feature API](feature-api.md) for operation semantics and geometric selectors.

## Write outcomes and retries

Successful operation batches return `ok`, `revision`, `trace_id`, definitions,
`body_count`, `bounds`, `volume`, `feature_errors`, and timings. They have already
built and saved the candidate and checkpointed its predecessor. Do not request a
second build/checkpoint for the same edit. Errors use `code` and `detail`; `detail`
is a string or an array of validation issues. Batch failures additionally expose
`ok: false`, `revision`, `trace_id`, `feature_errors`, and timings when available.

| Outcome | Client action |
| --- | --- |
| 400 / 403 | Correct host/origin or lifecycle authorization; do not loop |
| 404 | Verify resource ID and selected workspace |
| 409 | Read current definitions/revision and reconcile; never force overwrite |
| 422 | Fix identified input/geometry; rejected batch has not changed saved geometry |
| 500 / timeout / connection loss | Outcome uncertain; inspect trace and current revision before another write |

The client uses 15 seconds for ordinary requests, 120 seconds for a batch, and a
110-second status-wait deadline. The CAD worker has a 90-second limit, including
native-library import/startup, not just geometry computation; queue time
is additional, so a client timeout does not establish failure or cancel the edit.
Prefer one in-flight geometry batch per workspace. No automatic write retries.
Read-only requests may be retried with bounded backoff after service recovery.

Supply a unique `trace_id` with important batches. Reusing it returns 409; it is
duplicate detection, not an idempotent result-replay API. Read the trace after
losing a response. A `preparing` trace may become committed/rejected; after a crash
it may remain unresolved. Traces are local runtime evidence and can disappear if
caches are removed. Always inspect the saved design revision too. Creation and
checkpoint requests have no idempotency key: inspect lists/history before retries.

Custom-Python parameter changes save first and build asynchronously; inspect
`status` and `fingerprint`. Structured batches build before saving. Export geometry
only from a successful current build with its fingerprint. Source archives may
be retrieved without successful geometry. SSE is a notification mechanism, not a
durable event log: reread authoritative state on reconnect or gaps.

## Tested example

`scripts/example_part.py --port PORT` creates a blank, reads the revision, applies
a two-operation box, validates 20 × 16 × 10 mm geometry, and exports STEP to a
chosen output directory. The installation smoke test exercises this actual script
logic against the installed package. It never uses the viewer's active selection
as a write target. Run it only when you want a new example design.

```sh
voicedesign start
python scripts/example_part.py --port 8743 --output ./exports/example
```

The lifecycle shutdown endpoint is private to the launcher and excluded from the
public modeling contract. CAD endpoints assume a trusted local OS user; this API
does not provide remote multi-user authentication or untrusted Python execution.
