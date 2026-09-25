"""Small, deterministic feature language shared by the API and human editor.

This module deliberately imports CadQuery only when building. Discovering and
validating a feature document does not start the CAD kernel or execute Python.
Dimensions are millimetres; expressions are limited to named parameter references.
"""

from __future__ import annotations

import math
import re
from copy import deepcopy
from typing import Any

from voicedesign.cad import Feature, Model


class FeatureError(ValueError):
    """An actionable validation or geometry failure tied to a stable feature ID."""

    def __init__(self, feature_id: str | None, code: str, message: str):
        self.feature_id = feature_id
        self.code = code
        self.message = message
        super().__init__(f"{feature_id}: {message}" if feature_id else message)

    def as_dict(self) -> dict[str, Any]:
        return {"feature_id": self.feature_id, "code": self.code, "message": self.message}


_OPERATIONS = {
    "profile": {
        "description": "Closed 2D profile. Sketch coordinates are local to the selected plane.",
        "parameters": {
            "shape": {
                "type": "enum",
                "values": ["rectangle", "circle", "polygon"],
                "required": True,
            },
            "plane": {"type": "enum", "values": ["XY", "XZ", "YZ"], "default": "XY"},
            "origin": {"type": "vector3", "default": [0, 0, 0], "units": "mm"},
            "width": {
                "type": "number",
                "exclusive_min": 0,
                "units": "mm",
                "required_for": ["rectangle"],
            },
            "height": {
                "type": "number",
                "exclusive_min": 0,
                "units": "mm",
                "required_for": ["rectangle"],
            },
            "radius": {
                "type": "number",
                "exclusive_min": 0,
                "units": "mm",
                "required_for": ["circle"],
            },
            "points": {
                "type": "points2",
                "min_items": 3,
                "max_items": 256,
                "units": "mm",
                "required_for": ["polygon"],
            },
        },
        "constraints": [
            "Rectangles and circles are centred on origin.",
            "Polygon must be a simple, closed, nonzero-area outline; closure is implicit.",
            "Plane local axes: XY = +X,+Y; XZ = +X,+Z; YZ = +Y,+Z.",
        ],
        "example": {
            "id": "footprint",
            "type": "profile",
            "params": {"shape": "rectangle", "width": 70, "height": 70},
        },
    },
    "extrude": {
        "description": "Extrude a preceding profile along its plane normal.",
        "parameters": {
            "profile": {"type": "feature_ref", "feature_type": "profile", "required": True},
            "distance": {"type": "number", "units": "mm", "nonzero": True, "required": True},
            "operation": {
                "type": "enum",
                "values": ["new", "add", "cut", "intersect"],
                "default": "new",
            },
            "target": {
                "type": "feature_ref",
                "feature_type": "solid",
                "required_for": ["add", "cut", "intersect"],
            },
        },
        "constraints": [
            "Distance may be negative; zero is invalid.",
            "Non-new operations require an explicit target body lineage.",
        ],
        "example": {
            "id": "body",
            "type": "extrude",
            "params": {"profile": "footprint", "distance": 100},
        },
    },
    "fillet": {
        "description": "Round the target's semantically selected edges.",
        "parameters": {
            "target": {"type": "feature_ref", "feature_type": "solid", "required": True},
            "radius": {"type": "number", "units": "mm", "exclusive_min": 0, "required": True},
            "edges": {"type": "edge_query", "default": {"kind": "all"}},
        },
        "constraints": ["Selection must match edges, and radius must fit the geometry."],
        "example": {
            "id": "corners",
            "type": "fillet",
            "params": {"target": "body", "radius": 8, "edges": {"kind": "parallel", "axis": "Z"}},
        },
    },
    "shell": {
        "description": "Hollow the target inward and remove the selected extreme face(s).",
        "parameters": {
            "target": {"type": "feature_ref", "feature_type": "solid", "required": True},
            "thickness": {"type": "number", "units": "mm", "exclusive_min": 0, "required": True},
            "open_faces": {"type": "face_query", "required": True},
        },
        "constraints": [
            "Thickness is positive and measured inward.",
            "Selection must match faces, and thickness must fit the geometry.",
        ],
        "example": {
            "id": "cup",
            "type": "shell",
            "params": {
                "target": "corners",
                "thickness": 2.4,
                "open_faces": {"kind": "extreme", "axis": "Z", "side": "max"},
            },
        },
    },
    "boolean": {
        "description": "Combine target with explicit tool bodies; tool bodies are consumed.",
        "parameters": {
            "target": {"type": "feature_ref", "feature_type": "solid", "required": True},
            "tools": {
                "type": "feature_refs",
                "feature_type": "solid",
                "min_items": 1,
                "required": True,
            },
            "operation": {
                "type": "enum",
                "values": ["union", "cut", "intersect"],
                "required": True,
            },
        },
        "constraints": [
            "Target and tools must identify distinct live body lineages.",
            "All tools are consumed; unrelated bodies remain in the model.",
            "Intersect intersects the target with every tool.",
        ],
        "example": {
            "id": "joined",
            "type": "boolean",
            "params": {"target": "body", "tools": ["divider"], "operation": "union"},
        },
    },
}


def capabilities() -> dict[str, Any]:
    """Return the same constraints the interpreter enforces, without loading CAD."""
    return deepcopy(
        {
            "version": 1,
            "units": "mm",
            "number": {
                "type": "finite number or parameter reference",
                "reference": {"parameter": "width"},
                "description": "References resolve to current named parameter values; no eval.",
            },
            "feature": {
                "required": ["id", "type", "params"],
                "optional": {"name": "defaults to id", "suppressed": False},
                "id_pattern": "[A-Za-z][A-Za-z0-9_.-]{0,79}",
            },
            "operations": _OPERATIONS,
            "selection_queries": {
                "edge_query": [{"kind": "all"}, {"kind": "parallel", "axis": "X|Y|Z"}],
                "face_query": [{"kind": "extreme", "axis": "X|Y|Z", "side": "min|max"}],
                "description": "Axes are world coordinates. Extreme faces use face centre positions.",
            },
            "semantics": [
                "Features execute in document order; references must point to earlier active features.",
                "Solid feature IDs follow their body's current lineage after later modifications.",
                "Boolean tool lineages are consumed; referring to them later is an error.",
                "Profiles are reusable and have no solid preview; a profile-only document is empty.",
                "Every solid snapshot includes all live bodies; suppressed features do not execute.",
                "Custom geometry remains available through local model.py source mode.",
            ],
        }
    )


def _fail(fid: str | None, code: str, message: str):
    raise FeatureError(fid, code, message)


def _number(
    value: Any,
    parameters: dict,
    fid: str,
    key: str,
    *,
    positive: bool = False,
    nonzero: bool = False,
) -> float:
    if isinstance(value, dict):
        if set(value) != {"parameter"} or not isinstance(value["parameter"], str):
            _fail(fid, "invalid_parameter", f"{key} must be a number or {{'parameter': 'name'}}.")
        parameter = value["parameter"]
        if parameter not in parameters:
            _fail(fid, "missing_parameter", f"{key} references missing parameter '{parameter}'.")
        value = parameters[parameter]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(fid, "invalid_parameter", f"{key} must resolve to a finite number in mm.")
    try:
        value = float(value)
    except (OverflowError, ValueError):
        _fail(fid, "invalid_parameter", f"{key} must resolve to a finite number in mm.")
    if not math.isfinite(value):
        _fail(fid, "invalid_parameter", f"{key} must resolve to a finite number in mm.")
    if positive and value <= 0:
        _fail(fid, "invalid_parameter", f"{key} must be greater than zero mm.")
    if nonzero and value == 0:
        _fail(fid, "invalid_parameter", f"{key} must not be zero mm.")
    return value


def _vector(value: Any, length: int, parameters: dict, fid: str, key: str) -> list[float]:
    if not isinstance(value, list) or len(value) != length:
        _fail(fid, "invalid_parameter", f"{key} must contain exactly {length} coordinates.")
    return [_number(item, parameters, fid, f"{key}[{i}]") for i, item in enumerate(value)]


def _choice(value: Any, choices: list[str], fid: str, key: str):
    if value not in choices:
        _fail(fid, "invalid_parameter", f"{key} must be one of: {', '.join(choices)}.")


def _selection(value: Any, fid: str, key: str):
    if not isinstance(value, dict):
        _fail(fid, "invalid_selection", f"{key} must be a semantic selection query object.")
    if key == "edges" and value == {"kind": "all"}:
        return
    expected = {"kind", "axis"} if key == "edges" else {"kind", "axis", "side"}
    kind = "parallel" if key == "edges" else "extreme"
    if set(value) != expected or value.get("kind") != kind:
        _fail(fid, "invalid_selection", f"{key} must use the documented {kind} query.")
    _choice(value["axis"], ["X", "Y", "Z"], fid, f"{key}.axis")
    if key == "open_faces":
        _choice(value["side"], ["min", "max"], fid, f"{key}.side")


def _polygon(points: list[list[float]], fid: str):
    """Reject degenerate and self-intersecting outlines before reaching OpenCascade."""
    if points[0] == points[-1]:
        _fail(fid, "invalid_parameter", "points must omit the repeated closing point.")
    if len({tuple(p) for p in points}) != len(points):
        _fail(fid, "invalid_parameter", "points must not contain duplicate vertices.")
    area2 = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1]))
    if abs(area2) < 1e-12:
        _fail(fid, "invalid_parameter", "Polygon must enclose a nonzero area.")

    def cross(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def on_segment(a, b, p):
        return min(a[0], b[0]) <= p[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= p[1] <= max(
            a[1], b[1]
        )

    edges = list(zip(points, points[1:] + points[:1]))
    for i, (a, b) in enumerate(edges):
        for j in range(i + 2, len(edges)):
            if i == 0 and j == len(edges) - 1:
                continue
            c, d = edges[j]
            ab_c, ab_d, cd_a, cd_b = cross(a, b, c), cross(a, b, d), cross(c, d, a), cross(c, d, b)
            intersects = ab_c * ab_d < 0 and cd_a * cd_b < 0
            touches = any(
                v == 0 and on_segment(p, q, r)
                for v, p, q, r in (
                    (ab_c, a, b, c),
                    (ab_d, a, b, d),
                    (cd_a, c, d, a),
                    (cd_b, c, d, b),
                )
            )
            if intersects or touches:
                _fail(fid, "invalid_parameter", "Polygon edges must not cross or touch each other.")


def validate_features(definitions: list[dict], parameters: dict | None = None) -> list[dict]:
    """Normalize definitions and validate dimensions, schema, ordering and body ownership.

    This does not claim that geometry will regenerate: the build is authoritative
    for topology-dependent constraints such as a fillet's maximum possible radius.
    Suppressed definitions retain schema checks but do not resolve dependencies.
    """
    parameters = {} if parameters is None else parameters
    if not isinstance(parameters, dict):
        _fail(None, "invalid_parameter", "parameters must be a dictionary of named numeric values.")
    if not isinstance(definitions, list):
        _fail(None, "invalid_definition", "features must be an ordered list.")
    result = []
    seen: dict[str, dict] = {}
    lineage: dict[str, str] = {}
    live: set[str] = set()

    def reference(value: Any, fid: str, key: str, profile=False):
        if not isinstance(value, str) or value not in seen:
            _fail(
                fid, "invalid_reference", f"{key} must reference an earlier feature ID: {value!r}."
            )
        feature = seen[value]
        if feature["suppressed"]:
            _fail(fid, "suppressed_dependency", f"{key} references suppressed feature '{value}'.")
        if profile:
            if feature["type"] != "profile":
                _fail(fid, "invalid_reference", f"{key} must reference a profile, not '{value}'.")
            return value
        if value not in lineage:
            _fail(fid, "invalid_reference", f"{key} must reference a solid feature, not '{value}'.")
        if lineage[value] not in live:
            _fail(fid, "consumed_body", f"{key} references consumed tool body '{value}'.")
        return lineage[value]

    for index, definition in enumerate(definitions):
        if not isinstance(definition, dict):
            _fail(None, "invalid_definition", f"Feature at index {index} must be an object.")
        fid = definition.get("id")
        if not isinstance(fid, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,79}", fid):
            _fail(None, "invalid_definition", f"Feature at index {index} needs a valid stable id.")
        if fid in seen:
            _fail(fid, "duplicate_id", "Feature IDs must be unique within the document.")
        unknown = set(definition) - {"id", "type", "name", "suppressed", "params"}
        if unknown:
            _fail(
                fid, "invalid_definition", f"Unknown feature fields: {', '.join(sorted(unknown))}."
            )
        kind = definition.get("type")
        if not isinstance(kind, str) or kind not in _OPERATIONS:
            _fail(fid, "invalid_definition", f"Unsupported feature type {kind!r}.")
        name = definition.get("name", fid)
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            _fail(
                fid, "invalid_definition", "name must be nonempty text of at most 200 characters."
            )
        suppressed = definition.get("suppressed", False)
        if not isinstance(suppressed, bool):
            _fail(fid, "invalid_definition", "suppressed must be true or false.")
        p = deepcopy(definition.get("params"))
        if not isinstance(p, dict):
            _fail(fid, "invalid_definition", "params must be an object.")
        specs = _OPERATIONS[kind]["parameters"]
        unknown = set(p) - set(specs)
        if unknown:
            _fail(
                fid,
                "invalid_parameter",
                f"Unknown {kind} parameters: {', '.join(sorted(unknown))}.",
            )
        for key, spec in specs.items():
            if key not in p and "default" in spec:
                p[key] = deepcopy(spec["default"])
            if key not in p and spec.get("required"):
                _fail(fid, "missing_parameter", f"{kind} requires '{key}'.")

        if kind == "profile":
            _choice(p["shape"], ["rectangle", "circle", "polygon"], fid, "shape")
            _choice(p["plane"], ["XY", "XZ", "YZ"], fid, "plane")
            _vector(p["origin"], 3, parameters, fid, "origin")
            applicable = {
                "rectangle": ["width", "height"],
                "circle": ["radius"],
                "polygon": ["points"],
            }[p["shape"]]
            for key in applicable:
                if key not in p:
                    _fail(fid, "missing_parameter", f"{p['shape']} profile requires '{key}'.")
                if key != "points":
                    _number(p[key], parameters, fid, key, positive=True)
            for key in {"width", "height", "radius", "points"} - set(applicable):
                if key in p:
                    _fail(fid, "invalid_parameter", f"{key} is not used by {p['shape']} profiles.")
            if p["shape"] == "polygon":
                if not isinstance(p["points"], list) or not 3 <= len(p["points"]) <= 256:
                    _fail(
                        fid, "invalid_parameter", "points must contain between 3 and 256 vertices."
                    )
                points = [
                    _vector(v, 2, parameters, fid, f"points[{i}]")
                    for i, v in enumerate(p["points"])
                ]
                _polygon(points, fid)
        elif kind == "extrude":
            _number(p["distance"], parameters, fid, "distance", nonzero=True)
            _choice(p["operation"], ["new", "add", "cut", "intersect"], fid, "operation")
            if p["operation"] != "new" and "target" not in p:
                _fail(fid, "missing_parameter", f"{p['operation']} extrude requires 'target'.")
            if p["operation"] == "new" and "target" in p:
                _fail(fid, "invalid_parameter", "A new extrude has no target; omit 'target'.")
        elif kind == "fillet":
            _number(p["radius"], parameters, fid, "radius", positive=True)
            _selection(p["edges"], fid, "edges")
        elif kind == "shell":
            _number(p["thickness"], parameters, fid, "thickness", positive=True)
            _selection(p["open_faces"], fid, "open_faces")
        elif kind == "boolean":
            _choice(p["operation"], ["union", "cut", "intersect"], fid, "operation")
            if not isinstance(p["tools"], list) or not p["tools"]:
                _fail(
                    fid, "invalid_parameter", "tools must be a nonempty list of solid feature IDs."
                )

        normalized = {"id": fid, "type": kind, "name": name, "suppressed": suppressed, "params": p}
        if not suppressed and kind != "profile":
            if kind == "extrude":
                reference(p["profile"], fid, "profile", profile=True)
            if kind == "extrude" and p["operation"] == "new":
                body = fid
                live.add(body)
            else:
                body = reference(p["target"], fid, "target")
            if kind == "boolean":
                tools = [reference(tool, fid, f"tools[{i}]") for i, tool in enumerate(p["tools"])]
                if body in tools or len(set(tools)) != len(tools):
                    _fail(
                        fid, "invalid_reference", "Target and tools must be distinct body lineages."
                    )
                live.difference_update(tools)
            lineage[fid] = body
        seen[fid] = normalized
        result.append(normalized)
    return result


def build_features(definitions: list[dict], parameters: dict | None = None) -> Model:
    """Build fresh geometry from a validated feature document; never execute source."""
    parameters = {} if parameters is None else parameters
    definitions = validate_features(definitions, parameters)
    import cadquery as cq

    profiles: dict[str, dict] = {}
    lineage: dict[str, str] = {}
    live: dict[str, Any] = {}
    snapshots = []

    def profile_shape(definition):
        fid, p = definition["id"], definition["params"]
        origin = _vector(p["origin"], 3, parameters, fid, "origin")
        workplane = cq.Workplane(p["plane"], origin=tuple(origin))
        if p["shape"] == "rectangle":
            return workplane.rect(
                _number(p["width"], parameters, fid, "width"),
                _number(p["height"], parameters, fid, "height"),
            )
        if p["shape"] == "circle":
            return workplane.circle(_number(p["radius"], parameters, fid, "radius"))
        points = [_vector(point, 2, parameters, fid, "points") for point in p["points"]]
        return workplane.polyline(points).close()

    def valid_solid(shape, fid):
        solids = shape.Solids()
        if (
            not solids
            or not shape.isValid()
            or any(not s.isValid() or s.Volume() <= 0 for s in solids)
        ):
            _fail(
                fid,
                "invalid_geometry",
                "Operation produced no valid closed solid; check dimensions "
                "and whether the selected bodies overlap.",
            )
        # Some kernel operations return a compound wrapping a compsolid/shell.
        # Flatten to actual solids before a subsequent boolean; Compound.fuse
        # otherwise passes those non-solid children to OpenCascade.
        return solids[0] if len(solids) == 1 else cq.Compound.makeCompound(solids)

    def combine(target, tools, operation):
        if operation in {"union", "add"}:
            return target.fuse(*tools).clean()
        if operation == "cut":
            return target.cut(*tools).clean()
        result = target
        for tool in tools:
            result = result.intersect(tool)
        return result.clean()

    for definition in definitions:
        fid, kind, p = definition["id"], definition["type"], definition["params"]
        if definition["suppressed"]:
            continue
        if kind == "profile":
            profiles[fid] = definition
            continue
        try:
            consumed = []
            if kind == "extrude":
                # Build each profile afresh because CadQuery consumes pending wires.
                distance = _number(p["distance"], parameters, fid, "distance")
                shape = profile_shape(profiles[p["profile"]]).extrude(distance, combine=False).val()
                if p["operation"] == "new":
                    body = fid
                else:
                    body = lineage[p["target"]]
                    shape = combine(live[body], [shape], p["operation"])
            else:
                body = lineage[p["target"]]
                target = live[body]
                if kind == "fillet":
                    query = p["edges"]
                    selector = None if query["kind"] == "all" else f"|{query['axis']}"
                    selected = cq.Workplane(obj=target).edges(selector)
                    if not selected.size():
                        _fail(fid, "empty_selection", f"edges query {query} matched no edges.")
                    shape = selected.fillet(_number(p["radius"], parameters, fid, "radius")).val()
                elif kind == "shell":
                    query = p["open_faces"]
                    selector = (">" if query["side"] == "max" else "<") + query["axis"]
                    selected = cq.Workplane(obj=target).faces(selector)
                    if not selected.size():
                        _fail(fid, "empty_selection", f"open_faces query {query} matched no faces.")
                    shape = selected.shell(
                        -_number(p["thickness"], parameters, fid, "thickness")
                    ).val()
                else:
                    consumed = [lineage[tool] for tool in p["tools"]]
                    shape = combine(target, [live[tool] for tool in consumed], p["operation"])
            shape = valid_solid(shape, fid)
            for tool in consumed:
                del live[tool]
            lineage[fid] = body
            live[body] = shape
            snapshot = cq.Compound.makeCompound(
                [solid for value in live.values() for solid in value.Solids()]
            )
            valid_solid(snapshot, fid)
            snapshots.append(
                Feature(definition["name"], snapshot, kind, f"Structured {kind} ({fid})", id=fid)
            )
        except FeatureError:
            raise
        except Exception as error:  # noqa: BLE001 -- kernel exceptions become feature diagnostics
            _fail(
                fid,
                "geometry_error",
                f"{kind} could not regenerate: {error}. Check dimensions and semantic selections.",
            )
    return Model(snapshots)
