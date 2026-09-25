"""Installed command and workspace-scoped local service lifecycle."""

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import webbrowser
from contextlib import contextmanager
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from voicedesign import API_VERSION, __version__
from voicedesign.config import PACKAGE, STATIC, resolve_workspace, user_home


@contextmanager
def exclusive_lock(path):
    """An OS-held lock survives stale files but is released after process death."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError(f"Workspace is already in use (lock: {path}).") from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def health(port):
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
            return json.load(response)
    except (OSError, URLError, ValueError):
        return None


def record_path(workspace):
    return workspace.runtime / "server.json"


def read_record(workspace):
    try:
        return json.loads(record_path(workspace).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def running(workspace):
    record = read_record(workspace)
    if record:
        live = health(record["port"])
        if (
            live
            and live.get("app") == "voicedesign3d"
            and live.get("instance_id") == record["instance_id"]
            and live.get("workspace_path") == str(workspace.root)
        ):
            return {**live, "url": f"http://127.0.0.1:{record['port']}"}
    return None


def choose_port(requested):
    with socket.socket() as sock:
        if requested is not None:
            sock.bind(("127.0.0.1", requested))
        else:
            try:
                sock.bind(("127.0.0.1", 8743))
            except OSError:
                sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start(workspace, port=None):
    with exclusive_lock(workspace.runtime / "launch.lock"):
        live = running(workspace)
        if live:
            if port is not None and live["url"] != f"http://127.0.0.1:{port}":
                raise RuntimeError(f"This workspace is already running at {live['url']}.")
            if live["version"] != __version__:
                raise RuntimeError("A different app version is running. Stop it before upgrading.")
            return live
        if not (STATIC / "index.html").is_file():
            raise RuntimeError(
                "Viewer assets are missing. Install a release wheel or run npm run build."
            )
        port = choose_port(port)
        command = [
            sys.executable,
            "-m",
            "voicedesign",
            "--workspace",
            str(workspace.root),
            "--port",
            str(port),
            "serve",
        ]
        options = (
            {"creationflags": subprocess.CREATE_NO_WINDOW}
            if os.name == "nt"
            else {"start_new_session": True}
        )
        with (workspace.runtime / "server.log").open("ab") as log:
            process = subprocess.Popen(
                command,
                cwd=PACKAGE.parent,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                **options,
            )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if live := running(workspace):
                return live
            if process.poll() is not None:
                break
            time.sleep(0.2)
        raise RuntimeError(f"Startup did not complete. Inspect {workspace.runtime / 'server.log'}.")


def stop(workspace):
    live = running(workspace)
    if not live:
        return {"status": "stopped", "workspace_path": str(workspace.root)}
    record = read_record(workspace)
    request = Request(
        live["url"] + "/api/runtime/stop",
        data=b"{}",
        method="POST",
        headers={"Authorization": "Bearer " + record["token"], "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=5) as response:
        json.load(response)
    deadline = time.monotonic() + 110
    while time.monotonic() < deadline:
        if (read_record(workspace) or {}).get("instance_id") != record["instance_id"]:
            return {"status": "stopped", "workspace_path": str(workspace.root)}
        time.sleep(0.2)
    raise RuntimeError(
        "Shutdown is still waiting for work to finish. Check status before restarting."
    )


def serve(workspace, port=8743, container=False):
    import uvicorn

    from voicedesign.server import create_app
    from voicedesign.storage import atomic_write

    with exclusive_lock(workspace.root / ".voicedesign.lock"):
        token, instance = secrets.token_urlsafe(32), secrets.token_hex(16)
        app = create_app(workspace=workspace, instance_id=instance)
        server = uvicorn.Server(
            uvicorn.Config(
                app, host="0.0.0.0" if container else "127.0.0.1", port=port, log_level="warning"
            )
        )
        app.state.shutdown_token = token
        app.state.shutdown_callback = lambda: setattr(server, "should_exit", True)
        record = {"port": port, "instance_id": instance, "token": token}
        atomic_write(record_path(workspace), json.dumps(record))
        if os.name != "nt":
            record_path(workspace).chmod(0o600)
        try:
            server.run()
        finally:
            if (read_record(workspace) or {}).get("instance_id") == instance:
                record_path(workspace).unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--workspace", type=Path, help="Directory containing designs/ and exports/")
    parser.add_argument(
        "--port", type=int, help="Explicit local port; otherwise discover the workspace"
    )
    commands = parser.add_subparsers(dest="command")
    launch = commands.add_parser("start", help="Start or reuse this workspace without setup/build")
    launch.add_argument("--open", action="store_true", help="Open the viewer")
    commands.add_parser("status", help="Report workspace service status as JSON")
    commands.add_parser("stop", help="Gracefully stop only this workspace's verified service")
    commands.add_parser("doctor", help="Show installation and workspace configuration")
    foreground = commands.add_parser("serve", help="Run in the foreground")
    foreground.add_argument(
        "--container",
        action="store_true",
        help="Bind inside a container; publish only to host loopback",
    )
    commands.add_parser("init", help="Save this project's workspace pointer without overwriting")
    api = commands.add_parser("api", help="Call the running workspace (see api --help)")
    api.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535.")
    try:
        workspace = resolve_workspace(args.workspace)
        if args.command == "init":
            target = Path.cwd() / ".voicedesign.toml"
            # JSON strings are also valid TOML basic strings, including escaped Windows paths.
            root = str(args.workspace or "cad")
            with target.open("x", encoding="utf-8") as stream:
                stream.write("workspace = " + json.dumps(root) + "\n")
            result = {
                "project_config": str(target),
                "workspace_path": str(resolve_workspace().root),
            }
        elif args.command == "start":
            result = start(workspace, args.port)
            if args.open:
                webbrowser.open(result["url"])
        elif args.command == "stop":
            result = stop(workspace)
        elif args.command == "status":
            result = running(workspace) or {
                "status": "stopped",
                "workspace_path": str(workspace.root),
            }
        elif args.command == "doctor":
            result = {
                "version": __version__,
                "api_version": API_VERSION,
                "python": sys.executable,
                "package_path": str(PACKAGE),
                "workspace_path": str(workspace.root),
                "designs_path": str(workspace.designs),
                "runtime_path": str(workspace.runtime),
                "config_path": str(user_home() / "config.toml"),
                "viewer_installed": (STATIC / "index.html").is_file(),
                "service": running(workspace),
            }
        elif args.command == "api":
            from voicedesign.client import main as client_main

            live = running(workspace)
            if not live:
                raise RuntimeError("This workspace is stopped. Run voicedesign start first.")
            port = int(live["url"].rsplit(":", 1)[1])
            if args.port is not None and args.port != port:
                raise RuntimeError(
                    "Requested port does not match this workspace's running service."
                )
            client_main(["--port", str(port), *args.arguments])
            return
        else:
            serve(workspace, args.port or 8743, getattr(args, "container", False))
            return
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"voicedesign: {error}\n")
