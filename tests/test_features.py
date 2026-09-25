"""Geometry and document-contract tests for the deterministic feature API."""

import copy
import math
import subprocess
import sys

import pytest

from voicedesign.features import FeatureError, build_features, capabilities, validate_features


def feature(fid, kind, **params):
    return {"id": fid, "type": kind, "params": params}


def box(prefix="box", origin=None):
    return [
        feature(
            f"{prefix}_profile",
            "profile",
            shape="rectangle",
            width=10,
            height=10,
            origin=origin or [0, 0, 0],
        ),
        feature(prefix, "extrude", profile=f"{prefix}_profile", distance=10),
    ]


def test_discovery_and_validation_do_not_import_kernel():
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from voicedesign.features import capabilities, validate_features; "
                "assert capabilities()['units'] == 'mm'; assert validate_features([]) == []; "
                "assert 'cadquery' not in sys.modules; assert 'OCP' not in sys.modules"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    advertised = capabilities()
    assert set(advertised["operations"]) == {"profile", "extrude", "fillet", "shell", "boolean"}
    advertised["operations"]["profile"]["parameters"]["plane"]["default"] = "broken"
    assert capabilities()["operations"]["profile"]["parameters"]["plane"]["default"] == "XY"


def test_blank_and_profiles_only_have_no_solid_snapshots():
    assert build_features([]).features == []
    assert build_features([feature("sketch", "profile", shape="circle", radius=3)]).features == []


def test_full_four_compartment_cup_matches_independent_volume():
    reach, half = 33.8, 1.2
    cross = [
        (-reach, -half),
        (-half, -half),
        (-half, -reach),
        (half, -reach),
        (half, -half),
        (reach, -half),
        (reach, half),
        (half, half),
        (half, reach),
        (-half, reach),
        (-half, half),
        (-reach, half),
    ]
    definitions = [
        feature("footprint", "profile", shape="rectangle", width=70, height=70),
        feature("body", "extrude", profile="footprint", distance=100),
        feature(
            "rounded", "fillet", target="body", radius=8, edges={"kind": "parallel", "axis": "Z"}
        ),
        feature(
            "hollow",
            "shell",
            target="rounded",
            thickness=2.4,
            open_faces={"kind": "extreme", "axis": "Z", "side": "max"},
        ),
        feature("divider_profile", "profile", shape="polygon", points=[list(p) for p in cross]),
        feature(
            "divided",
            "extrude",
            profile="divider_profile",
            distance=92,
            operation="add",
            target="hollow",
        ),
    ]
    model = build_features(definitions)
    assert [f.id for f in model.features] == ["body", "rounded", "hollow", "divided"]
    assert all(f.shape.isValid() for f in model.features)
    shape = model.result
    bounds = shape.BoundingBox()
    assert [bounds.xlen, bounds.ylen, bounds.zlen] == pytest.approx([70, 70, 100])
    assert len(shape.Solids()) == 1
    outer_area = 70**2 - (4 - math.pi) * 8**2
    inner_width, inner_radius = 70 - 4.8, 8 - 2.4
    inner_area = inner_width**2 - (4 - math.pi) * inner_radius**2
    empty_cup = outer_area * 100 - inner_area * 97.6
    divider_area = 2 * inner_width * 2.4 - 2.4**2
    assert shape.Volume() == pytest.approx(empty_cup + divider_area * (92 - 2.4), abs=0.001)


def test_multiple_bodies_and_old_target_alias_follow_current_lineage():
    definitions = (
        box("a")
        + box("b", [8, 0, 0])
        + [
            feature("third_profile", "profile", shape="circle", radius=2, origin=[50, 0, 0]),
            feature("third", "extrude", profile="third_profile", distance=10),
            feature("merged", "boolean", target="a", tools=["b"], operation="union"),
            feature(
                "rounded", "fillet", target="a", radius=0.5, edges={"kind": "parallel", "axis": "Z"}
            ),
        ]
    )
    model = build_features(definitions)
    assert [len(f.shape.Solids()) for f in model.features] == [1, 2, 3, 2, 2]
    assert model.features[-2].shape.Volume() == pytest.approx(1800 + 40 * math.pi)
    # The third body persists, and using old ID 'a' fillets the union, not the original cube.
    assert model.result.Volume() == pytest.approx(1800 - (4 - math.pi) * 0.5**2 * 10 + 40 * math.pi)
    assert model.result.BoundingBox().xlen == pytest.approx(57)


@pytest.mark.parametrize(
    "operation,expected_volume",
    [
        ("cut", 1000 - 40 * math.pi),
        ("intersect", 40 * math.pi),
    ],
)
def test_boolean_consumes_tool_and_produces_expected_volume(operation, expected_volume):
    definitions = box() + [
        feature("hole_profile", "profile", shape="circle", radius=2),
        feature("tool", "extrude", profile="hole_profile", distance=10),
        feature("result", "boolean", target="box", tools=["tool"], operation=operation),
    ]
    result = build_features(definitions).result
    assert len(result.Solids()) == 1
    assert result.Volume() == pytest.approx(expected_volume)


@pytest.mark.parametrize(
    "operation,expected_volume",
    [
        ("cut", 1000 - 40 * math.pi),
        ("intersect", 40 * math.pi),
    ],
)
def test_extrude_operations_do_not_leave_tool_body(operation, expected_volume):
    definitions = box() + [
        feature("hole_profile", "profile", shape="circle", radius=2),
        feature(
            "result",
            "extrude",
            profile="hole_profile",
            distance=10,
            operation=operation,
            target="box",
        ),
    ]
    result = build_features(definitions).result
    assert len(result.Solids()) == 1
    assert result.Volume() == pytest.approx(expected_volume)


@pytest.mark.parametrize(
    "plane,expected_bounds",
    [
        ("XY", [10, 6, 4]),
        ("XZ", [10, 4, 6]),
        ("YZ", [4, 10, 6]),
    ],
)
def test_planes_signed_distance_and_parameter_references(plane, expected_bounds):
    definitions = [
        feature(
            "profile",
            "profile",
            shape="rectangle",
            plane=plane,
            origin=[-20, 0, {"parameter": "offset"}],
            width={"parameter": "width"},
            height=6,
        ),
        feature("body", "extrude", profile="profile", distance={"parameter": "depth"}),
    ]
    parameters = {"width": 10, "offset": -3, "depth": -4}
    normalized = validate_features(definitions, parameters)
    assert normalized[0]["params"]["width"] == {"parameter": "width"}
    bounds = build_features(definitions, parameters).result.BoundingBox()
    assert [bounds.xlen, bounds.ylen, bounds.zlen] == pytest.approx(expected_bounds)
    assert definitions[0].get("name") is None  # Caller data was not mutated.


def test_one_profile_can_be_extruded_more_than_once():
    definitions = [
        feature("sketch", "profile", shape="circle", radius=3),
        feature("above", "extrude", profile="sketch", distance=5),
        feature("below", "extrude", profile="sketch", distance=-5),
    ]
    result = build_features(definitions).result
    assert len(result.Solids()) == 2
    assert result.Volume() == pytest.approx(90 * math.pi)


def test_suppression_dependency_and_deleted_references_have_stable_errors():
    definitions = box()
    definitions[0]["suppressed"] = True
    with pytest.raises(FeatureError) as error:
        validate_features(definitions)
    assert error.value.as_dict() == {
        "feature_id": "box",
        "code": "suppressed_dependency",
        "message": "profile references suppressed feature 'box_profile'.",
    }
    definitions[1]["suppressed"] = True
    assert build_features(definitions).features == []
    with pytest.raises(FeatureError, match="earlier feature ID"):
        validate_features(box()[1:])


def test_consumed_body_and_alias_tool_cannot_be_reused():
    definitions = (
        box("a")
        + box("b", [8, 0, 0])
        + [
            feature("merge", "boolean", target="a", tools=["b"], operation="union"),
        ]
    )
    invalid = definitions + [feature("bad", "fillet", target="b", radius=1)]
    with pytest.raises(FeatureError) as error:
        validate_features(invalid)
    assert error.value.code == "consumed_body"
    invalid = definitions + [
        feature("bad", "boolean", target="a", tools=["merge"], operation="cut")
    ]
    with pytest.raises(FeatureError, match="distinct body lineages"):
        validate_features(invalid)


def test_empty_selection_and_impossible_fillet_report_the_failing_feature():
    definitions = [
        feature("circle", "profile", shape="circle", radius=5),
        feature("body", "extrude", profile="circle", distance=10),
        feature(
            "bad_edges", "fillet", target="body", radius=1, edges={"kind": "parallel", "axis": "X"}
        ),
    ]
    with pytest.raises(FeatureError) as error:
        build_features(definitions)
    assert error.value.code == "empty_selection"
    assert error.value.feature_id == "bad_edges"
    with pytest.raises(FeatureError) as error:
        build_features(box() + [feature("too_big", "fillet", target="box", radius=7)])
    assert error.value.code in {"geometry_error", "invalid_geometry"}
    assert error.value.feature_id == "too_big"


@pytest.mark.parametrize("distance", [0, True, float("inf"), float("nan"), "3 mm", {"eval": "2+3"}])
def test_bad_numeric_values_cannot_reach_kernel(distance):
    definitions = box()
    definitions[-1]["params"]["distance"] = distance
    with pytest.raises(FeatureError) as error:
        validate_features(definitions)
    assert error.value.feature_id == "box"
    assert error.value.code == "invalid_parameter"


def test_parameter_dependency_missing_and_invalid_selection():
    definitions = box()
    definitions[-1]["params"]["distance"] = {"parameter": "height"}
    with pytest.raises(FeatureError, match="missing parameter 'height'"):
        validate_features(definitions)
    definitions = box() + [
        feature("bad", "fillet", target="box", radius=1, edges={"kind": "index", "index": 3})
    ]
    with pytest.raises(FeatureError) as error:
        validate_features(definitions)
    assert error.value.code == "invalid_selection"


@pytest.mark.parametrize(
    "points",
    [
        [[0, 0], [5, 5], [0, 5], [5, 0]],  # crossing, zero signed area
        [[0, 0], [4, 4], [0, 3], [3, 0]],  # crossing, nonzero signed area
        [[0, 0], [1, 0], [2, 0]],  # collinear
        [[0, 0], [2, 0], [2, 2], [0, 0]],  # repeated closing vertex
    ],
)
def test_invalid_polygons_are_rejected_before_kernel(points):
    with pytest.raises(FeatureError):
        validate_features([feature("outline", "profile", shape="polygon", points=points)])


def test_duplicate_ids_unknown_fields_and_conditional_params_are_rejected():
    definitions = box()
    definitions.append(copy.deepcopy(definitions[0]))
    with pytest.raises(FeatureError) as error:
        validate_features(definitions)
    assert error.value.code == "duplicate_id"
    definitions = box()
    definitions[0]["params"]["radius"] = 3
    with pytest.raises(FeatureError, match="not used by rectangle"):
        validate_features(definitions)
    definitions = box()
    definitions[1]["params"]["operation"] = "cut"
    with pytest.raises(FeatureError, match="requires 'target'"):
        validate_features(definitions)
