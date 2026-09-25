"""Copy existing designs/history into a new workspace; never overwrite or remove originals."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def hashes(root):
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def migrate(source, workspace):
    source, workspace = source.resolve(), workspace.resolve()
    target = workspace / "designs"
    if target.exists() or source == workspace or source in workspace.parents:
        raise ValueError(
            "Choose a new destination outside the source; existing designs are never overwritten."
        )
    if not source.is_dir() or not any(source.glob("*/design.json")):
        raise ValueError("Source must be an existing designs directory.")
    if any(path.is_symlink() or path.is_junction() for path in source.rglob("*")):
        raise ValueError("Review linked directories/files before migration.")
    before = hashes(source)
    shutil.copytree(source, target)
    if hashes(target) != before or hashes(source) != before:
        raise RuntimeError("Hash verification failed; originals retained. Review the destination.")
    manifest = workspace / "migration-manifest.json"
    manifest.write_text(
        json.dumps({"source": str(source), "destination": str(target), "files": before}, indent=2),
        encoding="utf-8",
    )
    return {"files": len(before), "verified": True, "manifest": str(manifest)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("workspace", type=Path)
    args = parser.parse_args()
    print(json.dumps(migrate(args.source, args.workspace), indent=2))
