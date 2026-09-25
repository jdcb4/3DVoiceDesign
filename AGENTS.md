# VoiceDesign3D contributor and agent notes

## Architecture

Local CadQuery 2.6.1/OpenCascade workbench, Python 3.12, Three.js viewer.
Use the host agent for conversation; do not add an LLM or microphone service.
Application code and bundled assets live in `voicedesign/`. User designs and
histories live in configurable workspaces outside the installed package.
See `docs/storage.md`, `docs/api.md`, and `docs/deployment.md`.

## Modeling

- Use the intended workspace and design ID, not a shared viewer selection.
- `voicedesign start` starts/reuses the configured workspace. `voicedesign api`
  resolves its service. Prefer `features` and revision-checked `apply` batches.
- Related geometry changes belong in one batch: validate, build, checkpoint, commit.
  Failed candidates and stale revisions do not replace saved geometry.
- Read focused `/api/capabilities?operation=...` help for unfamiliar operations.
  Treat conflicts as a request to reread/reconcile. Inspect uncertain write outcomes.
- Structured `design.json.features` is authoritative. Editing `model.py` deliberately
  switches to trusted Python mode; no automatic graph conversion exists.
- For Python mode, read source/dimensions, checkpoint, edit, then wait for the
  current build. Keep each model self-contained; checkpoints cover only model.py
  and design.json. Use meaningful parameters and geometric selectors.
- Preserve saved designs/history. Never export stale geometry as current.
- End routine modeling edits after successful verification. Export/print reports
  belong to explicit requests. Preserve dirty human UI edits.

## Development and release

- `uv sync --frozen`, `npm ci`, `npm run build`, `uv build --out-dir artifacts`.
- `uv run pytest -q`, `uv run ruff check voicedesign tests scripts`, `npm run test:ui`.
- Browser tests use temporary workspaces. Verify wheel installation outside this
  checkout using `scripts/smoke_installed.py` and the installed environment's Python.
- Use `voicedesign ... serve` for foreground development; backend edits need restart.
- Keep uv.lock and package-lock.json committed. Preserve the tested casadi 3.7.2 /
  numpy 2.2.6 pair unless upgrades are explicitly validated on Windows.
- Startup must not install dependencies/build the viewer. Package assets with wheels.
- Loopback-only by default. Container mode requires explicit host-loopback publishing.
  Trusted Python is not sandboxed. No model-code upload endpoint, cloud service,
  telemetry, credentials, or CDN runtime dependencies.
- Do not run multiple servers/offline writers against one workspace. The launcher
  owns workspace locking. Runtime caches are disposable; design history is not.
- Structured models use a serial recyclable worker; Python uses a disposable process.
  Nested timing phases must not be summed as independent wall-clock durations.
- Skill source is `integrations/codex/voicedesign3d`. Do not install/update a personal
  skill unless the user explicitly requests it. Current release work edits source only.
- Keep personal designs, reference data, credentials, and experiments out of Git.
