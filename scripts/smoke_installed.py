"""Run with the installed tool's Python, outside the checkout import path."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from example_part import create_example, request


def main():
    with tempfile.TemporaryDirectory(prefix="voicedesign-install-") as directory:
        root = Path(directory)
        env = {**os.environ, "VOICEDESIGN_HOME": str(root / "app-home")}
        env.pop("PYTHONPATH", None)

        def command(workspace, *args, check=True):
            result = subprocess.run(
                [sys.executable, "-m", "voicedesign", "--workspace", str(workspace), *args],
                cwd=root,
                env=env,
                check=False,
                capture_output=True,
                text=True,
                timeout=180,
            )
            if check and result.returncode:
                raise RuntimeError(result.stderr + result.stdout)
            return json.loads(result.stdout) if result.returncode == 0 else result

        first, second = root / "first", root / "second"
        try:
            doctor = command(first, "doctor")
            assert doctor["viewer_installed"], doctor
            assert "site-packages" in doctor["package_path"], doctor
            a = command(first, "start")
            assert command(first, "start")["instance_id"] == a["instance_id"]
            b = command(second, "start")
            assert a["url"] != b["url"]
            port = int(a["url"].rsplit(":", 1)[1])
            from urllib.request import urlopen

            for route in ("/", "/docs", "/openapi.json"):
                with urlopen(a["url"] + route, timeout=10) as response:
                    assert response.status == 200
            schema = request(port, "/openapi.json")
            assert schema["paths"]["/api/designs/{design_id}/operations"]["post"]["responses"][
                "200"
            ]
            created = create_example(port, root / "exports")
            assert command(second, "api", "list") == []
            assert (
                command(first, "api", "features", created["id"])["revision"] == created["revision"]
            )
            template = command(
                first, "api", "new", "Installed template", "--template", "mounting-plate"
            )
            state = command(first, "api", "status", template["id"], "--wait", "--brief")
            assert state["status"] == "ready" and state["stats"]["bounds"] == [80, 50, 6]
            command(
                first, "api", "export", created["id"], "source", "--output", str(root / "part.zip")
            )
            assert (root / "part.zip").is_file()
            command(first, "stop")
            # Windows refuses this rename until the server AND venv redirector exit.
            log = Path(doctor["runtime_path"]) / "server.log"
            log.rename(log.with_suffix(".previous.log"))
            assert command(first, "status")["status"] == "stopped"
            command(first, "start")
            assert (
                command(first, "api", "features", created["id"])["revision"] == created["revision"]
            )
            print(
                json.dumps(
                    {
                        "ok": True,
                        "workspace_isolation": True,
                        "template_bounds": [80, 50, 6],
                        "structured_bounds": [20, 16, 10],
                        "restart_preserves_revision": True,
                    }
                )
            )
        finally:
            command(first, "stop", check=False)
            command(second, "stop", check=False)


if __name__ == "__main__":
    main()
