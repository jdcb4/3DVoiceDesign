"""Executable API example; creates a new part in the explicitly selected service."""

import argparse
import json
import uuid
from pathlib import Path
from urllib.request import Request, urlopen


def request(port, path, body=None, method=None, binary=False):
    call = Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(call, timeout=120) as response:
        return response.read() if binary else json.load(response)


def create_example(port, output):
    health = request(port, "/api/health")
    assert health["api_version"] == "1", health
    design = request(port, "/api/designs", {"name": "API example", "template": None})["id"]
    base = f"/api/designs/{design}"
    graph = request(port, base + "/features")
    batch = {
        "expected_revision": graph["revision"],
        "trace_id": uuid.uuid4().hex,
        "actor": "agent",
        "operations": [
            {
                "op": "add",
                "feature": {
                    "id": "outline",
                    "type": "profile",
                    "params": {"shape": "rectangle", "width": 20, "height": 16},
                },
            },
            {
                "op": "add",
                "feature": {
                    "id": "body",
                    "type": "extrude",
                    "params": {"profile": "outline", "distance": 10},
                },
            },
        ],
    }
    result = request(port, base + "/operations", batch)
    assert result["ok"] and result["body_count"] == 1, result
    assert result["bounds"] == [20, 16, 10] and result["volume"] == 3200, result
    output.mkdir(parents=True, exist_ok=True)
    artifact = output / f"{design}.step"
    artifact.write_bytes(
        request(port, base + "/export/step?fingerprint=" + result["revision"], binary=True)
    )
    assert artifact.read_bytes().startswith(b"ISO-10303-21"), artifact
    return {"id": design, "revision": result["revision"], "path": str(artifact.resolve())}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--output", type=Path, default=Path("exports/example"))
    args = parser.parse_args()
    print(json.dumps(create_example(args.port, args.output), indent=2))
