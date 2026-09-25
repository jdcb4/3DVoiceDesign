---
name: voicedesign3d
description: Operate an installed VoiceDesign3D workbench to create or edit physical CAD models, inspect geometry, and export designs. Use when this app is selected; respect an explicitly chosen different CAD tool. Does not replace general design or print-preparation guidance.
---

# VoiceDesign3D

Draft portable app skill. Use the installed `voicedesign` command; do not assume a
source checkout or install/update a global skill as part of modeling.

## Connect to the intended workspace

Use the user's explicit workspace, otherwise the project's `.voicedesign.toml`.
`voicedesign doctor` reports paths without starting CAD. If no workspace has been
chosen, select a project-local `cad/` workspace or an explicitly configured central
library workspace; avoid placing unrelated projects in the default workspace by
accident. `voicedesign --workspace PATH init` records a project pointer without
overwriting an existing one. Workspace flags precede the subcommand.

Run `voicedesign start` when needed; it reuses this workspace or starts it without
setup/build. `voicedesign api ...` addresses the correct managed instance. Use
`voicedesign-client --port PORT ...` for an explicitly provided endpoint, including
Docker. Do not change endpoint/workspace when a request fails. Missing installation
requires the published wheel and uv; consult the project deployment guide.

Keep the intended design ID from the request, connection card, or `api new "Name"
--blank` response. A viewer selection can change in another task; it is not a new
write target. Use `api activate ID` to show a known design, respecting pending GUI
edits. Check `/api/health` API version 1 when using HTTP directly.

## Structured models

Read `api features ID` for mode, definitions, parameters, selection, and revision.
Apply a JSON batch using `api apply ID batch.json`. It includes `expected_revision`
and operations. Use actual stable feature IDs and parameter keys. Batch related
changes; use `set_parameters` for bound named dimensions rather than breaking the
binding. Quantities are millimeters.

The service validates/builds once, checkpoints once, and commits. Success fields
`ok`, `revision`, `feature_errors`, `body_count`, `bounds`, and `timings` already
verify the saved edit. Do not build/checkpoint again or export after routine edits.

On 409, reread and reconcile human edits. On 422, fix the identified operation;
saved geometry is intact. A timeout/lost response may mean the edit committed.
Inspect its trace (if supplied) and current revision before another write. No blind
write retries. Trace IDs detect duplicate requests but do not replay results.

For unfamiliar operations, fetch `/api/capabilities?operation=extrude` (or profile,
fillet, shell, boolean). Full schema: `/api/operations/schema`. HTTP response/error
schemas: `/openapi.json`; human-readable offline reference: `/docs`. Do not load
all schemas on every edit. Batches allow 120 seconds; queue time can exceed this.

## Python, history, and exports

For Python mode, read the exact `source_path` and `manifest_path` from agent-context
or `/source`, checkpoint with `api checkpoint ID "Before ..."`, edit those files,
then `api status ID --wait --brief`. Keep models self-contained. Changing a structured
model's model.py opts into trusted Python; there is no automatic graph conversion.
No source-upload API exists. Container filesystem paths need host-mount translation.

Use `api history`, `restore`, and `export` when requested. STL/STEP are geometry;
source ZIP preserves editable source and checkpoint history. Export only current
successful geometry. Honor explicit output paths; default CLI exports go under
the calling directory. Never run offline builds beside a running workspace server.

## Personal design context

Keep tool mechanics here. Read printer/material preferences from the user's
configured persistent reference when relevant, not from assumptions embedded in
this reusable skill. If `PERSISTENT_AGENT_REFERENCE` is configured, consult its
`3DModeling/README.md` when present; otherwise use an explicitly supplied reference.
Do not invent printer specifications from a model name. Record important part-specific
assumptions in print_notes and keep project requirements with the project.
A separate general 3D-design skill can call this app skill; no such dependency is
required or automatically installed by this draft.
