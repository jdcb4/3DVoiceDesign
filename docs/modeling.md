# Familiar CAD operations in this workspace

## Starting blank

Choose **New design → Blank model**, or run
`python -m voicedesign.client new "Name" --blank`. The API equivalent is
`POST /api/designs` with `{"name":"Name","template":null}`. The existing API
default remains the mounting plate when `template` is omitted.

A blank design has an empty parameter dictionary and `build(p)` returning
`Model([])`. It saves and checkpoints normally. Its ready result has `empty: true`,
no features, and `stats: null`. STL/STEP exports become available after you add a
solid; the editable source archive is available immediately. To begin modeling,
add dimensions to `design.json` and return named solid features from `model.py`.

The **AI connection** card shows the selected model ID and API endpoint. Its copy
button includes exact local source paths and the supported editing workflow;
**View instructions** also makes the text available for manual copying.

## Modeling operations

Use the standard CadQuery API inside `build(p)`. Do not translate Onshape
FeatureScript verbatim; transfer the geometric intent, workplane, dimensions,
and feature ordering into Python. The [official API reference](https://cadquery.readthedocs.io/en/stable/apireference.html)
and [examples](https://cadquery.readthedocs.io/en/stable/examples.html) are the
source of truth for signatures and selectors.

| CAD concept | CadQuery equivalent | Practical note |
| --- | --- | --- |
| Sketch plane | `cq.Workplane("XY")`, `"XZ"`, `"YZ"` | Default Z is up; start on the print bed. |
| Sketch geometry | `.rect()`, `.circle()`, `.polyline().close()`, `.spline()` | Closed wires define solid profiles. |
| Sketch constraints | `cq.Sketch().constrain(...).solve()` | Available in Python; no constraint UI yet. |
| Extrude / add | `.extrude(distance)` | On a selected face, combines with the existing body by default. |
| Extrude / remove | `.cutBlind(-depth)`, `.cutThruAll()` | Start a profile on the relevant face. |
| Through hole | `.faces(">Z").workplane().hole(diameter)` | Prefer vertical holes for support-free printing. |
| Counterbore / countersink | `.cboreHole(...)`, `.cskHole(...)` | Check entry diameter, depth, and angle. |
| Fillet / chamfer | `.edges(selector).fillet(radius)` / `.chamfer(size)` | Use geometric selectors and model-level bounds checks. |
| Shell | `.faces(">Z").shell(-wall)` | Removes selected faces and offsets inward. |
| Revolve | `.revolve(360, axisStart, axisEnd)` | Axis points are in the sketch plane. |
| Loft | profiles on successive `.workplane(offset=...)`, then `.loft()` | Keep profiles compatible and non-intersecting. |
| Sweep | `profile.sweep(path)` | Match profile orientation to the path start. |
| Linear / circular pattern | `.rarray(...)`, `.polarArray(...)`, `.pushPoints(...)` | Place profiles or holes at repeated locations. |
| Boolean add/remove/common | `.union(other)`, `.cut(other)`, `.intersect(other)` | Solids must overlap where the operation requires it. |
| Mirror / transform | `.mirror("YZ")`, `.translate(...)`, `.rotate(...)` | Keep feature placement tied to dimensions. |
| Imported solid | `cq.importers.importStep(path)` | Underlying library capability only; assets are not yet tracked by this app. |

## Useful selectors

`">Z"` selects the highest face/edge in Z; `"<Z"` selects the lowest.
`"|Z"` selects edges parallel to Z. Select the face before creating its workplane.
Prefer these geometric meanings to array indices, which can change after edits.
Complicated topology can still change selector results; inspect the resulting part.

## Through-hole pattern and a pocket

```python
body = cq.Workplane("XY").rect(p["width"], p["depth"]).extrude(p["thickness"])
body = body.faces(">Z").workplane().rarray(40, 25, 2, 2).hole(5)
body = body.faces(">Z").workplane().rect(20, 10).cutBlind(-2)
```

Record a `Feature` after each solid-producing operation to expose intermediate
stages in the viewer. A standalone sketch has no volume and cannot be a visible
feature yet; record its extruded/revolved result instead. The last recorded solid
is the export target. Example templates demonstrate extrusion, holes, corner
fillets, chamfers, shelling, and revolve in complete editable models.

## Building for print

Use millimeters and name fit-critical dimensions. Put intended print orientation
and the support requirement in `print_notes`. The built-in solid check validates
CAD geometry; it does not automatically certify printability or structural safety.
Check STL manifoldness and overhangs separately before a final print.
