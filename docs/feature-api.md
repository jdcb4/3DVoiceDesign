# Structured feature API

Human forms and agents edit the same feature list in `design.json`. The app
interprets it using fixed CadQuery functions. There is no LLM or generated-Python
step inside the app. Existing custom Python models still work.

## Quick start

Use `voicedesign start` in the configured project, then `voicedesign api
new "My part" --blank`, `voicedesign api features DESIGN_ID`, and
`voicedesign api apply DESIGN_ID batch.json`. The project workspace determines
which local service is used. The low-level client accepts an explicit `--port`.
See [API contract](api.md) for connection, response/error schemas, and retry rules.

Example `batch.json` (replace the revision with the current value):

```json
{"expected_revision":"CURRENT_REVISION","actor":"agent","label":"Create base","operations":[
  {"op":"add","feature":{"id":"outline","type":"profile","params":{"shape":"rectangle","width":80,"height":50}}},
  {"op":"add","feature":{"id":"base","type":"extrude","params":{"profile":"outline","distance":6}}}
]}
```

Then a dimension change is one small operation:

```json
{"expected_revision":"LATEST_REVISION","operations":[
  {"op":"update","id":"base","changes":{"params":{"distance":8}}}
]}
```

Success returns `ok`, `revision`, definitions, `feature_errors`, `body_count`,
`bounds` in mm, `volume` in mm³, `timings` in seconds and a `trace_id`. The client
also records HTTP duration. This response already verifies and saves the edit;
no second build, checkpoint or status polling is needed.

## Feature vocabulary

Definitions have stable `id`, `type`, optional `name`/`suppressed`, and `params`.
IDs survive renaming and dimension edits. References use IDs.

| Type | Parameters |
| --- | --- |
| profile | shape rectangle/circle/polygon; plane XY/XZ/YZ; origin [x,y,z]; rectangle width/height, circle radius, or polygon points [[u,v],...] |
| extrude | profile ID, signed distance; operation new/add/cut/intersect; target solid ID for non-new |
| fillet | target, radius, edges {kind:all} or {kind:parallel,axis:X/Y/Z} |
| shell | target, positive inward thickness, open_faces {kind:extreme,axis:X/Y/Z,side:min/max} |
| boolean | target, tools [solid IDs], operation union/cut/intersect |

Rectangle/circle profiles are centered on origin. Polygon closure is implicit;
self-intersection and zero area are rejected. XY uses local X/Y, XZ uses X/Z,
YZ uses Y/Z; extrusion follows the plane normal. All lengths are mm. A numeric
field can instead bind to `{"parameter":"width"}`. Sketch-only graphs are valid
saved models without solid geometry; extrude to create a solid.

Earlier solid IDs refer to their current body lineage. New extrusions remain
separate bodies until a boolean consumes them. Final snapshots include all live
bodies. Missing, later, suppressed and consumed references fail explicitly.

`GET /api/capabilities?operation=shell` returns one operation's parameters,
defaults, units, constraints and example. Omit the query for the compact catalog.
Full batch schema: `GET /api/operations/schema`; HTTP schema: `/openapi.json`.

## Atomic batches and shared selection

`POST /api/designs/<id>/operations` requires `expected_revision` and `operations`:

- add: feature, optional insertion index.
- update: id, changes (name, suppressed, params). Params merge by default;
  `changes.replace_params:true` replaces them when switching a profile shape.
- remove: id. Remove/update dependent features in the same batch.
- move: id, zero-based final index. Dependencies must remain before consumers.
- set_parameters: values of existing named dimensions.
- define_parameter: name, parameter {label,value,min,max,step?,unit?,description?}.
- remove_parameter: name. Remaining references must still resolve.

The candidate validates/builds before taking one checkpoint and saving. Revision
checks run before building and again at commit. HTTP 409 requires rereading and
reconciling; HTTP 422 leaves saved source, dimensions and history intact. Never
blindly replay uncertain writes. A supplied trace_id must be unique; inspect its
trace and current model state before retrying.

GET `/api/designs/<id>/selection` exposes feature ID and optional picked point and
normal. PUT that endpoint with expected_revision, feature_id, optional point/normal
and actor human/agent to share selection. Picks expire on revision changes.
Mesh picks/rulers are approximate, not persistent B-rep face IDs or exact metrology.

GET `/api/events` streams `change` SSE events after commits and selection updates.
The viewer keeps polling for local source edits and fallback. A human form retains
its base revision and pending fields on conflicts. Saved history restores the graph
and dimensions; concurrent edits are detected, not automatically merged.

## Timing and custom code

GET `/api/traces/<trace_id>` separates preparation, queue, import, kernel, solid
checks, mesh, export, process and API duration. These are nested intervals and must
not all be added together. Client HTTP and browser load/render are separate.
Optional agent_timing planning_ms/tool_dispatch_ms must be caller-measured. Missing
observations stay null: the server cannot see Codex voice capture/internal reasoning.

For structured models, the initial `model.py` stays unchanged and `design.json.features`
is authoritative. Changing the local Python file selects trusted custom-code mode,
which stays in a disposable process. Source archives/checkpoints retain the saved
graph even when custom code is active. No arbitrary Python is accepted over HTTP
and no automatic Python-to-feature conversion is attempted.
