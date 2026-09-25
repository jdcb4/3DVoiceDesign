# VoiceDesign3D

A local, conversation-driven parametric CAD workbench using CadQuery/OpenCascade
and a Three.js viewer. Agents and the human editor share revision-checked geometry
operations. No AI service, microphone service, cloud account, or API key is required.

## Install a release

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then download
the wheel and `requirements-runtime.txt` from
[GitHub Releases](https://github.com/jdcb4/3DVoiceDesign/releases).

```powershell
uv tool install --python 3.12 --constraints .\requirements-runtime.txt .\voicedesign3d-0.1.0-py3-none-any.whl
voicedesign --version
voicedesign doctor
```

uv installs Python/native CAD dependencies in an isolated environment. The wheel
contains the viewer, templates, and offline API reference; Node.js is not needed
at runtime. First installation requires internet and several hundred MB of
dependencies. If needed, run `uv tool update-shell` and open a new terminal.
Windows is the primary local target; Linux is covered by CI.

## Choose where your designs live

A workspace contains `designs/<id>/model.py`, `design.json`, checkpoint histories,
and optional `exports/`. It is separate from the installed application.

For a project-local workspace, run in the project directory:

```powershell
voicedesign init
voicedesign start --open
voicedesign api new "My part" --blank
voicedesign api list
```

`init` writes `.voicedesign.toml` with `workspace = "cad"`. Alternatively, use a
central library with one workspace per project:

```powershell
voicedesign --workspace "D:\CADLibrary\sensor-case" init
voicedesign start --open
```

Commands discover the nearest project configuration, including from subfolders.
An explicit `--workspace` overrides it. Without project configuration, use
`VOICEDESIGN_WORKSPACE`, a user default, or the app's per-user default workspace.
See [storage and printer configuration](docs/storage.md).

```powershell
voicedesign status
voicedesign api features DESIGN_ID
voicedesign api apply DESIGN_ID batch.json
voicedesign api export DESIGN_ID step --output .\part.step
voicedesign stop
```

`start` reuses the selected workspace without installing or building anything.
Separate workspaces can run on automatically selected local ports. Use `--port
8745` before the subcommand to require a port. `api` discovers the correct instance;
explicit design IDs prevent cross-project selection mistakes. The low-level
`voicedesign-client --port 8745 ...` is also available.

## Agent interface

- [API v1 contract, errors, retries, and runnable example](docs/api.md)
- [Structured feature operations](docs/feature-api.md)
- [CAD modeling vocabulary](docs/modeling.md)
- [Draft skill and integration choices](docs/codex-setup.md)

The service exposes `/docs` (offline reference), `/openapi.json`,
`/api/capabilities`, and `/api/operations/schema`. A batch builds before saving,
preserves a checkpoint, and rejects stale revisions. The viewer's AI connection
card identifies the exact design and workspace.

Structured features cover profiles, extrusion, fillets, shells, and booleans.
Advanced geometry uses trusted local CadQuery Python. The app is not a Python
sandbox: use trusted sources. There is no source-upload endpoint. Keep the service
local; do not expose it directly to a LAN or the internet.

STL and STEP export geometry; source ZIPs preserve source, dimensions, and history.
Back up workspaces, not the installation. Failed builds cannot be exported as
current geometry. See [deployment and upgrades](docs/deployment.md).

## Build from source

Requires uv, Python 3.12 (uv can install it), and Node.js 22.12+.

```powershell
uv sync --frozen
npm ci
npm run build
uv build --out-dir artifacts
uv tool install --python 3.12 .\artifacts\voicedesign3d-0.1.0-py3-none-any.whl
```

For development, `uv run voicedesign --workspace ./cad serve` and `npm run dev`
provide backend and hot reload. `Start.ps1` is a checkout convenience launcher:
it installs/builds when prerequisites are missing or `-Setup` is specified. It
never installs a skill. Backend changes require a restart.

```powershell
uv run pytest -q
uv run ruff check voicedesign tests
npm run build
npm run test:ui
```

Tests use temporary workspaces. The pinned casadi/numpy combination preserves
the verified Windows CAD runtime.

## Docker

```sh
docker compose up --build -d
```

Compose publishes only `127.0.0.1:8743` and persists designs in a named volume.
Use a bind-mounted workspace when agents need to edit custom Python files.
See [Docker details](docs/deployment.md#docker).

## Distribution

Source and packages are published through GitHub. Personal designs, exports,
configuration, benchmark artifacts, and credentials are excluded.
[Third-party notices](THIRD_PARTY_NOTICES.md) describe bundled components.
The project's own license has not yet been selected; no open-source license is
granted by this initial publication. Third-party components retain their licenses.
