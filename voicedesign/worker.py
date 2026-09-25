"""CAD process protocol, shared by isolated source and reusable structured builds.

Models are trusted local Python with normal user filesystem permissions. Process
separation is a timeout/crash boundary, NOT a security sandbox. The persistent
mode accepts structured data only and never evaluates supplied Python.
"""

import contextlib
import gc
import json
import os
import runpy
import sys
import time
import traceback
from itertools import pairwise
from pathlib import Path

IMPORT_STARTED = time.perf_counter()
import cadquery as cq
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib

from voicedesign.cad import Feature, Model

IMPORT_SECONDS = time.perf_counter() - IMPORT_STARTED


def solid_shape(value):
    if isinstance(value, cq.Workplane):
        solids = value.solids().vals()
        if not solids:
            raise ValueError("Feature contains no solid. Extrude or revolve the sketch first.")
        value = cq.Compound.makeCompound(solids)
    if not isinstance(value, cq.Shape) or not value.Solids() or not value.isValid():
        raise ValueError("Feature must contain valid closed CAD solids.")
    if value.Volume() <= 0:
        raise ValueError("Feature has zero or negative volume.")
    return value


def mesh(shape):
    vertices, triangles = shape.tessellate(0.08, 0.15)
    positions = [coordinate for point in vertices for coordinate in point.toTuple()]
    indices = [index for triangle in triangles for index in triangle]
    lines = []
    for edge in shape.Edges():
        count = 1 if edge.geomType() == "LINE" else max(16, min(128, int(edge.Length() / 0.8)))
        points = [edge.positionAt(i / count).toTuple() for i in range(count + 1)]
        for a, b in pairwise(points):
            lines.extend(a + b)
    return {"positions": positions, "indices": indices, "edges": lines}


def build(snapshot: Path, output: Path, timings=None, *, structured_only=False):
    timings = timings if timings is not None else {}
    for phase in ("validation_seconds", "mesh_seconds", "export_seconds"):
        timings.setdefault(phase, 0.0)
    request = json.loads(snapshot.read_text(encoding="utf-8"))
    started = time.perf_counter()
    try:
        if "features" in request:
            from voicedesign.features import build_features

            result = build_features(request["features"], request["parameters"])
        elif structured_only:
            raise ValueError("Persistent workers accept structured features only.")
        else:
            source_path = output / "model.py"
            source_path.write_text(request["source"], encoding="utf-8")
            namespace = runpy.run_path(str(source_path))
            if not callable(namespace.get("build")):
                raise TypeError("model.py must define build(p).")
            result = namespace["build"](request["parameters"])
        if not isinstance(result, Model):
            result = Model([Feature("Result", result)])
    finally:
        timings["kernel_seconds"] = time.perf_counter() - started
    if not result.features:
        (output / "result.json").write_text(
            json.dumps({"features": [], "stats": None, "empty": True}), encoding="utf-8"
        )
        return
    features = []
    for index, feature in enumerate(result.features):
        try:
            started = time.perf_counter()
            try:
                shape = solid_shape(feature.shape)
                # CAD bounds must not inherit a preview triangulation's deflection margin.
                bounding = Bnd_Box()
                BRepBndLib.AddOptimal_s(shape.wrapped, bounding, False, False)
                xmin, ymin, zmin, xmax, ymax, zmax = bounding.Get()
                info = {
                    "index": index,
                    "id": feature.id,
                    "name": feature.name,
                    "operation": feature.operation,
                    "description": feature.description,
                    "solids": len(shape.Solids()),
                    "volume": round(shape.Volume(), 3),
                    "bounds": [round(xmax - xmin, 3), round(ymax - ymin, 3), round(zmax - zmin, 3)],
                }
            finally:
                timings["validation_seconds"] += time.perf_counter() - started
            started = time.perf_counter()
            try:
                (output / f"feature-{index}.json").write_text(
                    json.dumps({**info, **mesh(shape)}), encoding="utf-8"
                )
            finally:
                timings["mesh_seconds"] += time.perf_counter() - started
            features.append(info)
        except Exception as error:
            wrapped = ValueError(f"Feature {index + 1} ({feature.name}): {error}")
            wrapped.feature_id = feature.id
            raise wrapped from error
    started = time.perf_counter()
    try:
        cq.exporters.export(shape, str(output / "model.stl"), tolerance=0.05, angularTolerance=0.1)
        cq.exporters.export(shape, str(output / "model.step"))
    finally:
        timings["export_seconds"] = time.perf_counter() - started
    (output / "result.json").write_text(
        json.dumps({"features": features, "stats": features[-1], "empty": False}), encoding="utf-8"
    )


def execute(snapshot, output, *, structured_only=False, import_seconds=0):
    """A fresh request and geometry namespace; artifacts are unique per build."""
    timings = {
        "import_seconds": import_seconds,
        "kernel_seconds": 0.0,
        "validation_seconds": 0.0,
        "mesh_seconds": 0.0,
        "export_seconds": 0.0,
    }
    started = time.perf_counter()
    output.mkdir(parents=True, exist_ok=True)
    try:
        build(snapshot, output, timings, structured_only=structured_only)
        return True
    except Exception as error:  # noqa: BLE001 -- model failures form the worker error protocol
        (output / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        detail = (
            error.as_dict()
            if callable(getattr(error, "as_dict", None))
            else {"feature_id": getattr(error, "feature_id", None), "message": str(error)}
        )
        (output / "error.json").write_text(json.dumps([detail]), encoding="utf-8")
        return False
    finally:
        timings["worker_seconds"] = time.perf_counter() - started
        (output / "timings.json").write_text(json.dumps(timings), encoding="utf-8")


def serve():
    # Load supported implementation once, before recording process baseline. No
    # arbitrary imported project helpers or Python payloads enter this process.
    from voicedesign import features  # noqa: F401

    baseline_cwd = Path.cwd()
    baseline_env = dict(os.environ)
    baseline_path = list(sys.path)
    baseline_modules = set(sys.modules)
    protocol = sys.stdout
    print(
        json.dumps({"ready": True, "import_seconds": time.perf_counter() - IMPORT_STARTED}),
        file=protocol,
        flush=True,
    )
    for line in sys.stdin:
        command = json.loads(line)
        output = Path(command["output"]).resolve()
        snapshot = Path(command["snapshot"]).resolve()
        output.mkdir(parents=True, exist_ok=True)
        try:
            # Protect the line protocol from Python library output and give every
            # build its own cwd. Fresh JSON plus build_features' local dictionaries
            # prevent geometry or parameter objects carrying into later builds.
            os.chdir(output)
            with (
                (output / "worker.log").open("w", encoding="utf-8") as log,
                contextlib.redirect_stdout(log),
                contextlib.redirect_stderr(log),
            ):
                ok = execute(snapshot, output, structured_only=True)
        finally:
            cleanup_started = time.perf_counter()
            os.chdir(baseline_cwd)
            os.environ.clear()
            os.environ.update(baseline_env)
            sys.path[:] = baseline_path
            # Only discard modules loaded from the per-build directory. Removing
            # newly lazy-loaded CAD dependencies can corrupt extension-module state.
            for name in set(sys.modules) - baseline_modules:
                module_file = getattr(sys.modules.get(name), "__file__", None)
                if module_file and Path(module_file).resolve().is_relative_to(output):
                    sys.modules.pop(name, None)
            gc.collect()
            cleanup_seconds = time.perf_counter() - cleanup_started
        print(
            json.dumps({"id": command["id"], "ok": ok, "cleanup_seconds": cleanup_seconds}),
            file=protocol,
            flush=True,
        )


if __name__ == "__main__":
    if sys.argv[1:] == ["--persistent"]:
        serve()
    else:
        successful = execute(Path(sys.argv[1]), Path(sys.argv[2]), import_seconds=IMPORT_SECONDS)
        sys.exit(0 if successful else 1)
