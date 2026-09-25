# Deployment and releases

## Installed local app

Build the viewer before the wheel: `npm ci`, `npm run build`, then
`uv build --out-dir artifacts`. Install using
`uv tool install --python 3.12 PATH_TO_WHEEL`. The wheel contains application assets;
uv installs native CAD dependencies separately. Release wheels need no Node.js.
A bare Git checkout must have its viewer built before installation.
Release attachments include `requirements-runtime.txt`, exported from uv.lock.
Use `--constraints requirements-runtime.txt` when installing/upgrading to reproduce
the dependency versions tested for that release. Installing only the wheel allows
compatible dependencies to resolve to newer versions.

Global commands are `voicedesign` and `voicedesign-client`. Options `--workspace`
and `--port` precede the subcommand. Use `start --open`, `status`, `stop`, `doctor`,
`init`, `api`, or foreground `serve`. `python -m voicedesign` is the same entry point.
The client does not start a guessed workspace. Startup does not install dependencies.

Service identity includes the workspace and a random instance identifier. Stop
uses an instance-specific local control token, not a guessed PID. The token is
held in the user's runtime directory; keep that directory private. Local users
with access to the same OS account are trusted. One OS-held workspace lock prevents
competing managed servers. Do not run multiple uvicorn workers or bypass the
launcher. `create_app` is a test/embedding factory; its caller owns exclusivity.

Health confirms API readiness. It does not prewarm CAD: the first geometry build
loads the kernel; subsequent structured builds reuse the worker.

## Upgrade, uninstall, and backup

1. Stop each workspace with the old installed command.
2. Back up complete `designs/` trees, including history, needed exports, and project
   pointers. Back up your personal printer reference separately.
3. Install using `uv tool install --force --python 3.12 PATH_TO_WHEEL`.
4. Run `doctor`, start the workspace, and verify a representative design.

`uv tool uninstall voicedesign3d` removes the tool environment, not workspace data.
Installation, upgrades, and uninstall do not delete workspaces. Retain old wheels
for rollback; older versions may not understand future design schemas. Version 0.1
uses design schema 1 and API contract 1. Incompatible schema upgrades must provide
explicit backed-up migrations.

## Docker

The Dockerfile builds the viewer in a Node stage, builds a wheel in a Python stage,
and installs locked dependencies in a non-root runtime. The Compose named volume
persists `/data`; caches live in the container user home and may be regenerated.
Keep the data volume when removing containers; deleting it deletes saved designs.

```sh
docker compose up --build -d
docker compose logs
docker compose stop
```

For custom Python modeling, mount a host workspace:

```sh
docker run --rm -p 127.0.0.1:8743:8743 --mount type=bind,source=/absolute/cad-workspace,target=/data voicedesign3d:local
```

On Linux, make the directory writable by container UID 10001. Returned source
paths are container paths: translate `/data` to the host mount. Structured edits
need no path translation. Use `voicedesign-client --port 8743 ...` from the host;
manage lifecycle with Docker. The host launcher does not discover container records.

`serve --container` binds all interfaces inside the container. Publish only to host
loopback, as above. This is not an authenticated remote CAD service. Host/origin
checks remain enabled. Use a dedicated workspace per container.

## Release checks

CI runs Python, lint, viewer, and browser checks on Windows/Linux, builds packages,
and exercises an installed wheel from outside the checkout. The smoke test checks
geometry, a custom-Python template, schemas, lifecycle, isolation, and exports.
A Docker job builds and tests a Linux container. Version tags run release checks
before attaching packages to GitHub Releases. No PyPI or container-registry push
is configured.

Inspect the staging list and package contents before tagging. Personal designs,
local configuration, credentials, and historical experiment artifacts stay out of
releases. Platform support claims must follow actual verification results.
