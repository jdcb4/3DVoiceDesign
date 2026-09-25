"""A revolved knob with a vertical shaft bore. Print broad base down."""

import cadquery as cq

from voicedesign.cad import Feature, Model


def build(p):
    radius, height, bore = p["diameter"] / 2, p["height"], p["bore"]
    if bore / 2 + 2 > radius * 0.72:
        raise ValueError("The bore must leave at least 2 mm of wall at the neck.")
    profile = [
        (0, 0),
        (radius, 0),
        (radius, height * 0.2),
        (radius * 0.72, height * 0.5),
        (radius * 0.72, height),
        (0, height),
    ]
    body = cq.Workplane("XZ").polyline(profile).close().revolve(360, (0, 0), (0, 1))
    features = [
        Feature(
            "Revolved profile",
            body,
            "revolve",
            "Spin a closed radial profile 360 degrees around Z.",
        )
    ]
    body = body.faces(">Z").workplane().hole(bore)
    features.append(Feature("Shaft bore", body, "hole", "Through bore on the rotation axis."))
    body = body.faces(">Z").edges().chamfer(0.5)
    features.append(Feature("Bore lead-in", body, "chamfer"))
    return Model(features)
