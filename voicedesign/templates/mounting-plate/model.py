"""A flat mounting plate. All dimensions are millimeters; print flat, holes vertical."""

import cadquery as cq

from voicedesign.cad import Feature, Model


def build(p):
    width, depth, thickness = p["width"], p["depth"], p["thickness"]
    radius, inset, diameter = p["corner_radius"], p["hole_inset"], p["hole_diameter"]
    if radius >= min(width, depth) / 2:
        raise ValueError("Corner radius must be smaller than half the plate's shortest side.")
    if inset <= diameter / 2 + 1.2 or inset >= min(width, depth) / 2 - diameter / 2:
        raise ValueError(
            "Hole inset must leave at least 1.2 mm of material and keep holes separated."
        )
    plate = cq.Workplane("XY").rect(width, depth).extrude(thickness)
    features = [
        Feature("Base extrusion", plate, "extrude", "Centered rectangle on XY, extruded upward.")
    ]
    plate = plate.edges("|Z").fillet(radius)
    features.append(Feature("Corner rounds", plate, "fillet", "Round the four vertical corners."))
    points = [(x * (width / 2 - inset), y * (depth / 2 - inset)) for x in (-1, 1) for y in (-1, 1)]
    plate = plate.faces(">Z").workplane().pushPoints(points).hole(diameter)
    features.append(
        Feature("Mounting holes", plate, "hole", "Four through holes, located from the edges.")
    )
    # A shallow rim breaks the sharp edge while keeping a flat, support-free base.
    plate = plate.faces(">Z").edges().chamfer(p["edge_chamfer"])
    features.append(
        Feature("Top edge chamfer", plate, "chamfer", "Chamfer the top perimeter and hole entries.")
    )
    return Model(features)
