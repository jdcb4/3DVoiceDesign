"""An open-top tray using a true shell feature. Print on its flat base."""

import cadquery as cq

from voicedesign.cad import Feature, Model


def build(p):
    width, depth, height, wall = p["width"], p["depth"], p["height"], p["wall"]
    if wall * 2 >= min(width, depth) or wall >= height:
        raise ValueError("Wall thickness leaves no interior space.")
    if p["corner_radius"] <= wall:
        raise ValueError("Outer corner radius must exceed wall thickness.")
    body = cq.Workplane("XY").rect(width, depth).extrude(height)
    features = [Feature("Outer envelope", body, "extrude", "Overall outside dimensions.")]
    body = body.edges("|Z").fillet(p["corner_radius"])
    features.append(Feature("Round corners", body, "fillet"))
    body = body.faces(">Z").shell(-wall)
    features.append(
        Feature("Open-top shell", body, "shell", "Remove the top face and offset the walls inward.")
    )
    return Model(features)
