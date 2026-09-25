"""Small loopback HTTP client for conversation-driven CAD. No extra dependencies."""

import argparse
import json
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class Client:
    def __init__(self, port=8743):
        if not 1 <= port <= 65535:
            raise ValueError("Port must be between 1 and 65535.")
        self.base = f"http://127.0.0.1:{port}"

    def request(self, path, method="GET", body=None, binary=False, timeout=15):
        data = json.dumps(body, allow_nan=False).encode() if body is not None else None
        request = Request(
            self.base + "/api" + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                content = response.read()
                return content if binary else json.loads(content)
        except HTTPError as error:
            content = error.read().decode()
            try:
                detail = json.loads(content).get("detail", content)
            except json.JSONDecodeError:
                detail = content
            raise RuntimeError(f"Workbench request failed ({error.code}): {detail}") from error
        except (URLError, TimeoutError) as error:
            raise RuntimeError(
                "Workbench unavailable or request timed out. A write may have committed; inspect "
                "the current revision/trace before retrying. Run voicedesign status."
            ) from error

    def design_path(self, design_id):
        return "/designs/" + quote(design_id, safe="")

    def status(self, design_id, wait=False):
        state = self.request(self.design_path(design_id))
        return self.wait_for_build(design_id, state) if wait else state

    def wait_for_build(self, design_id, state):
        """Wait for this revision, without another agent turn or a second CAD build."""
        deadline = time.monotonic() + 110
        expected = state["fingerprint"]
        while state["status"] == "building":
            if time.monotonic() >= deadline:
                raise TimeoutError("The model is still building. Check its status again shortly.")
            time.sleep(0.2)
            state = self.request(self.design_path(design_id))
            if state["fingerprint"] != expected:
                raise RuntimeError("The design changed during this build. Read its current state.")
        if state["status"] != "ready":
            raise RuntimeError(state.get("error") or "The current model failed to build.")
        return state

    def activate(self, design_id):
        return self.request("/workspace/active", "PUT", {"design_id": design_id})

    def new(self, name, template="mounting-plate", copy_from=None):
        body = {"name": name, "template": template, "copy_from": copy_from}
        created = self.request("/designs", "POST", body)
        self.activate(created["id"])
        return created

    def parameters(self, design_id, values, fingerprint=None, wait=False):
        expected = fingerprint or self.status(design_id)["fingerprint"]
        state = self.request(
            self.design_path(design_id) + "/parameters",
            "PUT",
            {"values": values, "fingerprint": expected},
        )
        return self.wait_for_build(design_id, state) if wait else state

    def checkpoint(self, design_id, label):
        return self.request(self.design_path(design_id) + "/history", "POST", {"label": label})

    def features(self, design_id):
        return self.request(self.design_path(design_id) + "/features")

    def apply(
        self,
        design_id,
        operations,
        expected_revision,
        *,
        label="Agent edit",
        actor="agent",
        trace_id=None,
        agent_timing=None,
    ):
        """Build and commit a whole feature edit in one CAD request, using a known revision."""
        payload = {
            "expected_revision": expected_revision,
            "operations": operations,
            "label": label,
            "actor": actor,
        }
        if trace_id:
            payload["trace_id"] = trace_id
        if agent_timing:
            payload["agent_timing"] = agent_timing
        started = time.perf_counter()
        result = self.request(
            self.design_path(design_id) + "/operations", "POST", payload, timeout=120
        )
        result["client_http_seconds"] = time.perf_counter() - started
        # One lightweight observation write; no second build or status polling.
        try:
            self.request(
                "/traces/" + result["trace_id"] + "/client",
                "POST",
                {"http_ms": result["client_http_seconds"] * 1000, **(agent_timing or {})},
            )
        except RuntimeError as error:
            result["timing_report_error"] = str(error)
        return result

    def export(self, design_id, format, output=None):
        if format not in ("stl", "step", "source"):
            raise ValueError("Supported exports: stl, step, source.")
        state = self.status(design_id, wait=format != "source")
        if format != "source" and state["status"] != "ready":
            raise RuntimeError(state["error"] or "The current model has not built successfully.")
        data = self.request(
            self.design_path(design_id) + f"/export/{format}?fingerprint={state['fingerprint']}",
            binary=True,
        )
        suffix = "-source.zip" if format == "source" else f".{format}"
        path = Path(output) if output else Path.cwd() / "exports" / design_id / (design_id + suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"path": str(path.resolve()), "bytes": len(data)}


def brief_state(state):
    """Keep the evidence needed to confirm an edit, without full feature metadata."""
    return {
        "id": state["id"],
        "status": state["status"],
        "fingerprint": state["fingerprint"],
        "build_seconds": state.get("seconds"),
        "parameters": {k: p["value"] for k, p in state["design"]["parameters"].items()},
        "stats": (state.get("result") or {}).get("stats"),
        "error": state.get("error"),
    }


def main(argv=None):
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8743)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("health", "workspace", "list", "templates", "capabilities"):
        commands.add_parser(name)
    features = commands.add_parser("features", help="Read the current feature graph and revision")
    features.add_argument("design")
    apply = commands.add_parser("apply", help="Apply a JSON batch; verifies and saves once")
    apply.add_argument("design")
    apply.add_argument(
        "file", type=Path, help="JSON BatchRequest with expected_revision and operations"
    )
    trace = commands.add_parser("trace", help="Read local per-stage edit timings")
    trace.add_argument("id")
    status = commands.add_parser("status")
    status.add_argument("design")
    status.add_argument("--wait", action="store_true")
    status.add_argument("--brief", action="store_true", help="Return compact build evidence")
    new = commands.add_parser("new")
    new.add_argument("name")
    new.add_argument("--template", default="mounting-plate")
    new.add_argument("--copy-from")
    new.add_argument("--blank", action="store_true", help="Start without geometry or dimensions")
    activate = commands.add_parser("activate")
    activate.add_argument("design")
    change = commands.add_parser("set")
    change.add_argument("design")
    change.add_argument("values", nargs="+", help="key=value, e.g. width=100")
    change.add_argument("--fingerprint", help="Optional revision from a previous status read")
    change.add_argument("--wait", action="store_true", help="Wait for the saved revision to build")
    change.add_argument("--brief", action="store_true", help="Return compact build evidence")
    checkpoint = commands.add_parser("checkpoint")
    checkpoint.add_argument("design")
    checkpoint.add_argument("label")
    history = commands.add_parser("history")
    history.add_argument("design")
    restore = commands.add_parser("restore")
    restore.add_argument("design")
    restore.add_argument("revision")
    restore.add_argument("--fingerprint", required=True)
    export = commands.add_parser("export")
    export.add_argument("design")
    export.add_argument("format", choices=["stl", "step", "source"])
    export.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    client = Client(args.port)
    if args.command in ("health", "workspace", "list", "templates", "capabilities"):
        result = client.request("/designs" if args.command == "list" else "/" + args.command)
    elif args.command == "features":
        result = client.features(args.design)
    elif args.command == "apply":
        payload = json.loads(args.file.read_text(encoding="utf-8-sig"))
        result = client.apply(args.design, **payload)
    elif args.command == "trace":
        result = client.request("/traces/" + quote(args.id, safe=""))
    elif args.command == "status":
        result = client.status(args.design, args.wait)
    elif args.command == "new":
        if args.blank and args.copy_from:
            parser.error("--blank cannot be combined with --copy-from")
        result = client.new(args.name, None if args.blank else args.template, args.copy_from)
    elif args.command == "activate":
        result = client.activate(args.design)
    elif args.command == "set":
        values = dict(value.split("=", 1) for value in args.values)
        result = client.parameters(
            args.design, {k: float(v) for k, v in values.items()}, args.fingerprint, args.wait
        )
    elif args.command == "checkpoint":
        result = client.checkpoint(args.design, args.label)
    elif args.command == "history":
        result = client.request(client.design_path(args.design) + "/history")
    elif args.command == "restore":
        result = client.request(
            client.design_path(args.design)
            + "/history/"
            + quote(args.revision, safe="")
            + "/restore",
            "POST",
            {"fingerprint": args.fingerprint},
        )
    else:
        result = client.export(args.design, args.format, args.output)
    if getattr(args, "brief", False):
        result = {**brief_state(result), "command_seconds": round(time.monotonic() - started, 3)}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
