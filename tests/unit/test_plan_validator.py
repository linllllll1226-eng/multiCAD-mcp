"""Unit tests for drawing plan safety and geometry checks."""

import math

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
