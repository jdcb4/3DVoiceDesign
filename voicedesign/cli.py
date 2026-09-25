"""Command line companion for Codex and local scripts. No running server required."""

import argparse
import json
import shutil
import time
from pathlib import Path

from voicedesign.config import TEMPLATES, resolve_workspace
from voicedesign.engine import Engine
from voicedesign.launcher import exclusive_lock
from voicedesign.storage import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List saved designs")
    build = commands.add_parser("build", help="Validate and export a design to STL and STEP")
    build.add_argument("design")
    build.add_argument("--output", type=Path, help="Export folder (default: exports/<design>)")
    checkpoint = commands.add_parser("checkpoint", help="Save editable source and parameter values")
    checkpoint.add_argument("design")
    checkpoint.add_argument("label")
    change = commands.add_parser(
        "set", help="Save one or more dimensions, with an automatic checkpoint"
    )
    change.add_argument("design")
    change.add_argument("values", nargs="+", help="key=value, e.g. width=100 thickness=8")
    new = commands.add_parser("new", help="Create a new editable design")
    new.add_argument("name")
    new.add_argument("--blank", action="store_true", help="Start without geometry or dimensions")
    new.add_argument(
        "--template",
        choices=["mounting-plate", "enclosure", "turned-knob"],
        default="mounting-plate",
    )
    args = parser.parse_args()
    workspace = resolve_workspace(args.workspace)
    with exclusive_lock(workspace.root / ".voicedesign.lock"):
        store = Store(workspace.designs)
        if args.command == "list":
            print(json.dumps(store.list(), indent=2))
        elif args.command == "checkpoint":
            print(json.dumps(store.checkpoint(args.design, args.label), indent=2))
        elif args.command == "new":
            print(store.create(args.name, None if args.blank else TEMPLATES / args.template))
        elif args.command == "set":
            _, _, fingerprint = store.read(args.design)
            values = dict(value.split("=", 1) for value in args.values)
            store.update_parameters(
                args.design, {k: float(v) for k, v in values.items()}, fingerprint
            )
            print("Parameters saved. An open workbench will rebuild automatically.")
        elif args.command == "build":
            engine = Engine(store, workspace.cache)
            try:
                state = engine.refresh(args.design)
                while state["status"] == "building":
                    time.sleep(0.2)
                    state = engine.refresh(args.design)
                if state["status"] != "ready":
                    raise RuntimeError(state["error"])
                output = (args.output or workspace.exports / args.design).resolve()
                output.mkdir(parents=True, exist_ok=True)
                for extension in () if state["result"].get("empty") else ("stl", "step"):
                    source = engine.artifact(
                        args.design, state["fingerprint"], f"model.{extension}"
                    )
                    shutil.copyfile(source, output / f"{args.design}.{extension}")
                print(
                    json.dumps(
                        {
                            "design": args.design,
                            "seconds": state["seconds"],
                            "output": str(output),
                            "stats": state["result"]["stats"],
                            "empty": state["result"].get("empty", False),
                        },
                        indent=2,
                    )
                )
            finally:
                engine.close()


if __name__ == "__main__":
    main()
