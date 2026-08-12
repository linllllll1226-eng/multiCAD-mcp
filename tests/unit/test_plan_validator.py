"""Unit tests for drawing plan safety and geometry checks."""

import math

import pytest

from cad_memory.models import DrawingPlan
from cad_memory.validator import PlanValidator

LAYERS = {
    "AI_PREVIEW_OUTLINE",
    "AI_PREVIEW_CENTER",
    "AI_PREVIEW_HIDDEN",
    "AI_PREVIEW_HATCH",
    "AI_PREVIEW_DIM",
    "AI_UNCERTAIN",
}


def validate(entities, **overrides):
    data = {
        "task_name": "test",
        "unit": "mm",
        "entities": entities,
        "existing_layers": sorted(LAYERS),
        "user_confirmed": True,
        "preview_mode": True,
    }
    data.update(overrides)
    return PlanValidator().validate(DrawingPlan.model_validate(data), available_layers=LAYERS)


def entity(kind, coordinates, dimensions, layer="AI_PREVIEW_OUTLINE", **extra):
    data = {
        "entity_type": kind,
        "coordinates": coordinates,
        "dimensions": dimensions,
        "layer": layer,
        "linetype": "ByLayer",
        "dimension_source": "explicit_dimension",
        "confidence": 1.0,
    }
    data.update(extra)
    return data


def test_1000_by_600_rectangle_and_centered_diameter_100_circle():
    report = validate(
        [
            entity("rectangle", {"corner1": [0, 0], "corner2": [1000, 600]}, {}),
            entity("circle", {"center": [500, 300]}, {"radius": 50}),
        ]
    )
    assert report.passed, report.to_dict()


def test_diameter_and_radius_dimensions_reject_manual_prefixes():
    diameter = entity(
        "diametric_dimension",
        {"chord_point": [7.5, 0], "far_chord_point": [-7.5, 0]},
        {"diameter": 15, "leader_length": 8, "measurement": 15},
        layer="AI_PREVIEW_DIM",
        text_override="Ø<>",
    )
    radius = entity(
        "radial_dimension",
        {"center": [0, 0], "chord_point": [15, 0]},
        {"radius": 15, "leader_length": 8, "measurement": 15},
        layer="AI_PREVIEW_DIM",
        text_override="R<>",
    )
    report = validate([diameter, radius])
    codes = {issue.code for issue in report.errors}
    assert "dimension_text_override" in codes


def test_true_diameter_and_radius_dimensions_allow_empty_override():
    items = [
        entity(
            "diametric_dimension",
            {"chord_point": [7.5, 0], "far_chord_point": [-7.5, 0]},
            {"diameter": 15, "leader_length": 8, "measurement": 15},
            layer="AI_PREVIEW_DIM",
        ),
        entity(
            "radial_dimension",
            {"center": [0, 0], "chord_point": [15, 0]},
            {"radius": 15, "leader_length": 8, "measurement": 15},
            layer="AI_PREVIEW_DIM",
        ),
    ]
    assert validate(items).passed


def test_parallel_line_distance_is_seven():
    report = validate(
        [
            entity(
                "line",
                {"start": [0, 3.5], "end": [100, 3.5]},
                {},
                constraints=[{"kind": "equal_distance", "data": {"distances": [7, 7]}}],
            ),
            entity("line", {"start": [0, -3.5], "end": [100, -3.5]}, {}),
        ]
    )
    assert report.passed
    assert abs(3.5 - (-3.5)) == 7


def test_three_holes_on_diameter_250_at_120_degrees():
    points = [
        [125 * math.cos(math.radians(angle)), 125 * math.sin(math.radians(angle))]
        for angle in (0, 120, 240)
    ]
    item = entity(
        "circle",
        {"center": points[0]},
        {"radius": 11},
        constraints=[
            {
                "kind": "uniform_distribution",
                "data": {"center": [0, 0], "points": points, "angle": 120},
                "tolerance": 1e-6,
            }
        ],
    )
    assert validate([item]).passed


def test_approximate_reference_cannot_use_formal_layer():
    item = entity(
        "line",
        {"start": [0, 0], "end": [1, 1]},
        {},
        dimension_source="approximate_reference",
        uncertain_items=["missing width"],
    )
    report = validate([item])
    codes = {issue.code for issue in report.errors}
    assert "approximate_on_formal_layer" in codes
    assert "unconfirmed_uncertain_geometry" in codes


def test_approximate_reference_allowed_only_on_ai_uncertain():
    item = entity(
        "line",
        {"start": [0, 0], "end": [1, 1]},
        {},
        layer="AI_UNCERTAIN",
        dimension_source="approximate_reference",
        uncertain_items=["missing width"],
    )
    assert validate([item]).passed


def test_dimension_layout_cannot_include_measured_geometry_coordinates():
    item = entity(
        "aligned_dimension",
        {"start": [0, 0], "end": [7, 0], "text_position": [10, 10]},
        {"measurement": 7},
        layer="AI_PREVIEW_DIM",
        operation="layout_only",
        target_handles=["ABC"],
    )
    report = validate([item])
    assert any(issue.code == "layout_changes_geometry" for issue in report.errors)


def test_dimension_layout_accepts_only_text_position_and_target_handle():
    item = entity(
        "aligned_dimension",
        {"text_position": [10, 10]},
        {"measurement": 7},
        layer="AI_PREVIEW_DIM",
        operation="layout_only",
        target_handles=["ABC"],
    )
    assert validate([item]).passed


def test_missing_unit_and_negative_dimension_block_execution():
    item = entity("circle", {"center": [0, 0]}, {"radius": -1})
    report = validate([item], unit=None)
    codes = {issue.code for issue in report.errors}
    assert {"unit_missing", "dimension_nonpositive", "circle_radius_invalid"} <= codes


@pytest.mark.parametrize(
    "item",
    [
        entity("line", {"start": [0, 0], "end": [1, 0]}, {}),
        entity("text", {"position": [0, 0]}, {"height": 2.5, "rotation": 0}, text_override="A"),
        entity(
            "rectangle",
            {"corner1": [0, 0], "corner2": [2, 1]},
            {"width": 2, "height": 1, "area": 2},
        ),
        entity("circle", {"center": [0, 0]}, {"radius": 1}),
        entity("arc", {"center": [0, 0]}, {"radius": 1, "start_angle": 350, "end_angle": 10}),
        entity("polyline", {"points": [[0, 0], [1, 0]]}, {"closed": False}),
        entity(
            "aligned_dimension",
            {"start": [0, 0], "end": [1, 0]},
            {"measurement": 1, "offset": 1},
            layer="AI_PREVIEW_DIM",
        ),
        entity(
            "linear_dimension",
            {"start": [0, 0], "end": [0, 1]},
            {"measurement": 1, "offset": 1},
            layer="AI_PREVIEW_DIM",
        ),
        entity(
            "diametric_dimension",
            {"chord_point": [1, 0], "far_chord_point": [-1, 0]},
            {"diameter": 2, "leader_length": 1},
            layer="AI_PREVIEW_DIM",
        ),
        entity(
            "radial_dimension",
            {"center": [0, 0], "chord_point": [1, 0]},
            {"radius": 1, "leader_length": 1},
            layer="AI_PREVIEW_DIM",
        ),
    ],
    ids=[
        "line",
        "text",
        "rectangle",
        "circle",
        "arc",
        "polyline",
        "aligned-dimension",
        "linear-dimension",
        "diametric-dimension",
        "radial-dimension",
    ],
)
def test_supported_entity_schemas_accept_valid_geometry(item):
    assert validate([item]).passed


@pytest.mark.parametrize(
    ("item", "expected_code"),
    [
        (entity("line", {"start": [0, 0], "end": [0, 0]}, {}), "line_zero_length"),
        (
            entity("text", {"position": [0, 0]}, {}, text_override="A"),
            "dimension_missing",
        ),
        (
            entity("rectangle", {"corner1": [0, 0], "corner2": [0, 1]}, {}),
            "rectangle_width_nonpositive",
        ),
        (entity("circle", {"center": [0, 0]}, {"radius": 0}), "circle_radius_invalid"),
        (
            entity(
                "arc",
                {"center": [0, 0]},
                {"radius": 1, "start_angle": 10, "end_angle": 10},
            ),
            "arc_sweep_invalid",
        ),
        (
            entity(
                "polyline",
                {"points": [[0, 0], [1, 0], [1, 0]]},
                {"closed": False},
            ),
            "polyline_zero_length_segment",
        ),
        (
            entity(
                "aligned_dimension",
                {"start": [0, 0], "end": [0, 0]},
                {},
                layer="AI_PREVIEW_DIM",
            ),
            "dimension_defining_points_coincident",
        ),
        (
            entity(
                "linear_dimension",
                {"start": [0, 0], "end": [0, 0]},
                {},
                layer="AI_PREVIEW_DIM",
            ),
            "dimension_defining_points_coincident",
        ),
        (
            entity(
                "diametric_dimension",
                {"chord_point": [0, 0], "far_chord_point": [0, 0]},
                {},
                layer="AI_PREVIEW_DIM",
            ),
            "dimension_defining_points_coincident",
        ),
        (
            entity(
                "radial_dimension",
                {"center": [0, 0], "chord_point": [0, 0]},
                {},
                layer="AI_PREVIEW_DIM",
            ),
            "dimension_defining_points_coincident",
        ),
    ],
    ids=[
        "line",
        "text",
        "rectangle",
        "circle",
        "arc",
        "polyline",
        "aligned-dimension",
        "linear-dimension",
        "diametric-dimension",
        "radial-dimension",
    ],
)
def test_supported_entity_schemas_reject_degenerate_geometry(item, expected_code):
    report = validate([item])
    assert not report.passed
    assert expected_code in {issue.code for issue in report.errors}


def test_open_polyline_keeps_boolean_closure_state_separate_from_dimensions():
    item = entity("polyline", {"points": [[0, 0], [1, 0]]}, {"closed": False})
    plan = DrawingPlan.model_validate(
        {
            "task_name": "typed-polyline",
            "unit": "mm",
            "entities": [item],
            "existing_layers": sorted(LAYERS),
            "user_confirmed": True,
            "preview_mode": True,
        }
    )

    assert plan.entities[0].dimensions["closed"] is False
    assert PlanValidator().validate(plan, available_layers=LAYERS).passed


def test_closed_polyline_requires_three_unique_vertices_and_no_duplicate_closure():
    item = entity(
        "polyline",
        {"points": [[0, 0], [1, 0], [0, 0]]},
        {"closed": True},
    )
    report = validate([item])
    codes = {issue.code for issue in report.errors}
    assert {
        "polyline_repeated_vertex",
        "polyline_unique_vertices_invalid",
        "polyline_zero_length_segment",
    } <= codes


def test_polyline_rejects_repeated_nonconsecutive_vertex():
    item = entity(
        "polyline",
        {"points": [[0, 0], [1, 0], [1, 1], [1, 0], [2, 0]]},
        {"closed": False},
    )
    report = validate([item])
    assert any(issue.code == "polyline_repeated_vertex" for issue in report.errors)


@pytest.mark.parametrize("closed", [False, True], ids=["open", "closed"])
def test_2d_polyline_rejects_vertices_that_differ_only_in_z(closed):
    points = [[0, 0, 0], [0, 0, 1]]
    if closed:
        points.append([1, 0, 0])
    item = entity("polyline", {"points": points}, {"closed": closed})
    report = validate([item])
    codes = {issue.code for issue in report.errors}
    assert "polyline_repeated_vertex" in codes
    assert "polyline_zero_length_segment" in codes


@pytest.mark.parametrize("kind", ["aligned_dimension", "linear_dimension"])
def test_linear_dimension_allows_negative_offset_for_opposite_side(kind):
    item = entity(
        kind,
        {"start": [0, 0], "end": [1, 0]},
        {"measurement": 1, "offset": -10},
        layer="AI_PREVIEW_DIM",
    )
    assert validate([item]).passed


def test_entity_schema_rejects_mistyped_dimension_field():
    report = validate([entity("line", {"start": [0, 0], "end": [1, 0]}, {"length": 1})])
    assert any(issue.code == "dimension_field_unsupported" for issue in report.errors)
