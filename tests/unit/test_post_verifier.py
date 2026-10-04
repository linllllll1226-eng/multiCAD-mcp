"""Unit tests for actual-object verification without AutoCAD."""

import json
import math
from fractions import Fraction

import pytest

from cad_memory.models import DrawingPlan
from cad_memory.verifier import PostExecutionVerifier, read_entity_state


def strict_report(result):
    """Use the standard strict encoder as an independent output contract check."""
    encoded = json.dumps(result, allow_nan=False)
    assert json.loads(encoded) == result
    assert all(row["error"] is None or math.isfinite(row["error"]) for row in result["rows"])


def entity_plan(kind, coordinates, dimensions, layer="AI_PREVIEW_OUTLINE"):
    """Build a valid plan whose actual COM observations can be varied separately."""
    return DrawingPlan.model_validate(
        {
            "task_name": "strict-json",
            "unit": "mm",
            "user_confirmed": True,
            "existing_layers": [layer],
            "entities": [
                {
                    "entity_type": kind,
                    "coordinates": coordinates,
                    "dimensions": dimensions,
                    "layer": layer,
                    "linetype": "ByLayer",
                    "dimension_source": "explicit_dimension",
                    "confidence": 1,
                }
            ],
        }
    )


class FakeCircle:
    Handle = "A1"
    ObjectName = "AcDbCircle"
    Layer = "AI_PREVIEW_OUTLINE"
    Linetype = "ByLayer"
    Center = (500.0, 300.0, 0.0)
    Radius = 50.0


class FakeDimension:
    Handle = "D1"
    ObjectName = "AcDbDiametricDimension"
    Layer = "AI_PREVIEW_DIM"
    Linetype = "ByLayer"
    Measurement = 15.0
    TextOverride = ""
    TextFill = False


class FakeDiametricDimension(FakeDimension):
    """Diametric dimension exposing both defining chord points."""

    Measurement = 10.0
    ChordPoint = (0.0, 0.0, 0.0)
    FarChordPoint = (10.0, 0.0, 0.0)


class FakeAlignedDimension:
    """Aligned dimension returned for a guarded linear-dimension request."""

    Handle = "D2"
    ObjectName = "AcDbAlignedDimension"
    Layer = "AI_PREVIEW_DIM"
    Linetype = "ByLayer"
    Measurement = 60.0
    TextOverride = ""
    TextFill = False
    ExtLine1Point = (0.0, 0.0, 0.0)
    ExtLine2Point = (60.0, 0.0, 0.0)


class FakeMovedAlignedDimension(FakeAlignedDimension):
    """Aligned dimension whose first defining point was moved."""

    ExtLine1Point = (1.0, 0.0, 0.0)


class FakeRectangle:
    """Closed rectangular lightweight polyline returned by AutoCAD."""

    Handle = "R1"
    ObjectName = "AcDb2dPolyline"
    Layer = "AI_PREVIEW_OUTLINE"
    Linetype = "ByLayer"
    Closed = True
    Coordinates = (
        0.0,
        0.0,
        0.0,
        1000.0,
        0.0,
        0.0,
        1000.0,
        600.0,
        0.0,
        0.0,
        600.0,
        0.0,
        0.0,
        0.0,
        0.0,
    )


class FakeShiftedRectangle(FakeRectangle):
    """Same-size rectangle translated away from the planned position."""

    Coordinates = (
        100.0,
        100.0,
        0.0,
        110.0,
        100.0,
        0.0,
        110.0,
        105.0,
        0.0,
        100.0,
        105.0,
        0.0,
        100.0,
        100.0,
        0.0,
    )


class FakePolyline:
    """Lightweight polyline double with flattened 2D coordinates."""

    Handle = "P1"
    ObjectName = "AcDbPolyline"
    Layer = "AI_PREVIEW_OUTLINE"
    Linetype = "ByLayer"

    def __init__(self, coordinates, closed=False):
        """Store the actual coordinates and closure state."""
        self.Coordinates = coordinates
        self.Closed = closed


class FakeArc:
    """Arc double exposing AutoCAD's radians-based angle properties."""

    Handle = "A2"
    ObjectName = "AcDbArc"
    Layer = "AI_PREVIEW_OUTLINE"
    Linetype = "ByLayer"
    Center = (0.0, 0.0, 0.0)
    Radius = 5.0

    def __init__(self, start_angle, end_angle):
        """Store angles in the same radians representation as AutoCAD COM."""
        self.StartAngle = math.radians(start_angle)
        self.EndAngle = math.radians(end_angle)


class FakeLine:
    Handle = "L1"
    ObjectName = "AcDbLine"
    Layer = "AI_PREVIEW_CENTER"
    Linetype = "ByLayer"
    StartPoint = (0.0, 0.0, 0.0)
    EndPoint = (100.0, 0.0, 0.0)
    Length = 100.0


class FakeLayer:
    """Layer double carrying one effective linetype."""

    def __init__(self, linetype):
        """Store the layer linetype."""
        self.Linetype = linetype


class FakeLayers:
    """Layers collection double returning one configured layer."""

    def __init__(self, linetype="Continuous"):
        """Store the effective linetype for all requested layers."""
        self.linetype = linetype

    def Item(self, _name):  # noqa: N802 - mirrors AutoCAD COM
        return FakeLayer(self.linetype)


class FakeDocument:
    """Minimal COM document double."""

    def __init__(self, objects, layer_linetype="Continuous"):
        """Store fake entities by handle."""
        self.objects = objects
        self.Layers = FakeLayers(layer_linetype)

    def HandleToObject(self, handle):  # noqa: N802 - mirrors AutoCAD COM
        """Return a fake entity by COM-style handle lookup."""
        return self.objects[handle]


class FakeAdapter:
    """Minimal adapter double used by the verifier."""

    def __init__(self, objects, layer_linetype="Continuous"):
        """Create the adapter with a fake document."""
        self.document = FakeDocument(objects, layer_linetype)

    def _get_document(self, operation):
        return self.document


def test_read_dimension_state_has_empty_override_and_no_fill():
    state = read_entity_state(FakeDimension())
    assert state["measurement"] == 15
    assert state["text_override"] == ""
    assert state["background_fill"] is False


def test_actual_circle_matches_center_and_diameter():
    plan = DrawingPlan.model_validate(
        {
            "task_name": "circle",
            "unit": "mm",
            "user_confirmed": True,
            "existing_layers": ["AI_PREVIEW_OUTLINE"],
            "entities": [
                {
                    "entity_type": "circle",
                    "coordinates": {"center": [500, 300, 0]},
                    "dimensions": {"radius": 50},
                    "layer": "AI_PREVIEW_OUTLINE",
                    "linetype": "ByLayer",
                    "dimension_source": "explicit_dimension",
                    "confidence": 1,
                }
            ],
        }
    )
    result = PostExecutionVerifier().verify(FakeAdapter({"A1": FakeCircle()}), plan, ["A1"])
    assert result["passed"], result
    properties = {row["property"] for row in result["rows"]}
    assert {
        "center",
        "radius",
        "diameter",
        "layer",
        "linetype",
        "object_type",
    } <= properties


def test_2d_plan_point_matches_autocad_xyz_point():
    """An omitted zero elevation must not make a valid 2D line fail."""
    plan = DrawingPlan.model_validate(
        {
            "task_name": "2d-line",
            "unit": "mm",
            "user_confirmed": True,
            "existing_layers": ["AI_PREVIEW_CENTER"],
            "entities": [
                {
                    "entity_type": "line",
                    "coordinates": {"start": [0, 0], "end": [100, 0]},
                    "dimensions": {},
                    "layer": "AI_PREVIEW_CENTER",
                    "linetype": "ByLayer",
                    "dimension_source": "explicit_dimension",
                    "confidence": 1,
                }
            ],
        }
    )
    result = PostExecutionVerifier().verify(
        FakeAdapter({"L1": FakeLine()}, "CENTER2"), plan, ["L1"]
    )
    assert result["passed"], result


def test_linear_dimension_accepts_aligned_dimension_from_guarded_executor():
    """The executor's aligned COM object is valid for linear dimensions."""
    plan = DrawingPlan.model_validate(
        {
            "task_name": "linear-dimension",
            "unit": "mm",
            "user_confirmed": True,
            "existing_layers": ["AI_PREVIEW_DIM"],
            "entities": [
                {
                    "entity_type": "linear_dimension",
                    "coordinates": {"start": [0, 0], "end": [60, 0]},
                    "dimensions": {"measurement": 60, "offset": 10},
                    "layer": "AI_PREVIEW_DIM",
                    "linetype": "ByLayer",
                    "dimension_source": "explicit_dimension",
                    "confidence": 1,
                }
            ],
        }
    )
    result = PostExecutionVerifier().verify(
        FakeAdapter({"D2": FakeAlignedDimension()}), plan, ["D2"]
    )
    assert result["passed"], result


def test_actual_rectangle_reports_width_height_and_closed_state():
    """Verify rectangle size from real polyline coordinates, not plan intent."""
    plan = DrawingPlan.model_validate(
        {
            "task_name": "rectangle",
            "unit": "mm",
            "user_confirmed": True,
            "existing_layers": ["AI_PREVIEW_OUTLINE"],
            "entities": [
                {
                    "entity_type": "rectangle",
                    "coordinates": {"corner1": [0, 0, 0], "corner2": [1000, 600, 0]},
                    "dimensions": {"width": 1000, "height": 600},
                    "layer": "AI_PREVIEW_OUTLINE",
                    "linetype": "ByLayer",
                    "dimension_source": "explicit_dimension",
                    "confidence": 1,
                }
            ],
        }
    )
    result = PostExecutionVerifier().verify(FakeAdapter({"R1": FakeRectangle()}), plan, ["R1"])
    assert result["passed"], result
    rows = {row["property"]: row for row in result["rows"]}
    assert rows["width"]["actual"] == 1000
    assert rows["height"]["actual"] == 600
    assert rows["closed"]["actual"] is True


def test_shifted_same_size_rectangle_fails_vertex_verification():
    """A rectangle with matching dimensions but a different position must fail."""
    plan = DrawingPlan.model_validate(
        {
            "task_name": "shifted-rectangle",
            "unit": "mm",
            "user_confirmed": True,
            "existing_layers": ["AI_PREVIEW_OUTLINE"],
            "entities": [
                {
                    "entity_type": "rectangle",
                    "coordinates": {"corner1": [0, 0], "corner2": [10, 5]},
                    "dimensions": {"width": 10, "height": 5},
                    "layer": "AI_PREVIEW_OUTLINE",
                    "linetype": "ByLayer",
                    "dimension_source": "explicit_dimension",
                    "confidence": 1,
                }
            ],
        }
    )
    result = PostExecutionVerifier().verify(
        FakeAdapter({"R1": FakeShiftedRectangle()}), plan, ["R1"]
    )
    assert not result["passed"]
    row = next(item for item in result["rows"] if item["property"] == "vertices")
    assert row["actual"][0] == [100.0, 100.0, 0.0]
    assert any("vertices" in error and "expected" in error for error in result["errors"])


def test_polyline_vertex_change_fails_ordered_geometry_verification():
    """A changed vertex must fail even when the polyline has the same count."""
    plan = DrawingPlan.model_validate(
        {
            "task_name": "polyline",
            "unit": "mm",
            "user_confirmed": True,
            "existing_layers": ["AI_PREVIEW_OUTLINE"],
            "entities": [
                {
                    "entity_type": "polyline",
                    "coordinates": {"points": [[0, 0], [10, 0], [10, 5]]},
                    "dimensions": {"closed": False},
                    "layer": "AI_PREVIEW_OUTLINE",
                    "linetype": "ByLayer",
                    "dimension_source": "explicit_dimension",
                    "confidence": 1,
                }
            ],
        }
    )
    actual = FakePolyline((0.0, 0.0, 10.0, 0.0, 11.0, 5.0))
    result = PostExecutionVerifier().verify(FakeAdapter({"P1": actual}), plan, ["P1"])
    assert not result["passed"]
    row = next(item for item in result["rows"] if item["property"] == "vertices")
    assert row["actual"][-1] == [11.0, 5.0, 0.0]


def test_arc_sweep_change_fails_normalized_angle_verification():
    """Arc start/end angles must be compared rather than only center/radius."""
    plan = DrawingPlan.model_validate(
        {
            "task_name": "arc",
            "unit": "mm",
            "user_confirmed": True,
            "existing_layers": ["AI_PREVIEW_OUTLINE"],
            "entities": [
                {
                    "entity_type": "arc",
                    "coordinates": {"center": [0, 0]},
                    "dimensions": {"radius": 5, "start_angle": 0, "end_angle": 90},
                    "layer": "AI_PREVIEW_OUTLINE",
                    "linetype": "ByLayer",
                    "dimension_source": "explicit_dimension",
                    "confidence": 1,
                }
            ],
        }
    )
    result = PostExecutionVerifier().verify(FakeAdapter({"A2": FakeArc(0, 180)}), plan, ["A2"])
    assert not result["passed"]
    row = next(item for item in result["rows"] if item["property"] == "end_angle")
    assert row["target"] == 90.0
    assert row["actual"] == 180.0
    assert row["error"] == 90.0


def test_arc_angle_wrap_is_treated_as_equivalent():
    """Equivalent angles beyond one full turn must pass normalization."""
    plan = DrawingPlan.model_validate(
        {
            "task_name": "wrapped-arc",
            "unit": "mm",
            "user_confirmed": True,
            "existing_layers": ["AI_PREVIEW_OUTLINE"],
            "entities": [
                {
                    "entity_type": "arc",
                    "coordinates": {"center": [0, 0]},
                    "dimensions": {"radius": 5, "start_angle": 0, "end_angle": 90},
                    "layer": "AI_PREVIEW_OUTLINE",
                    "linetype": "ByLayer",
                    "dimension_source": "explicit_dimension",
                    "confidence": 1,
                }
            ],
        }
    )
    result = PostExecutionVerifier().verify(FakeAdapter({"A2": FakeArc(360, 450)}), plan, ["A2"])
    assert result["passed"], result


def test_dimension_definition_point_change_fails_verification():
    """Moving an aligned dimension extension point must fail verification."""
    plan = DrawingPlan.model_validate(
        {
            "task_name": "linear-dimension",
            "unit": "mm",
            "user_confirmed": True,
            "existing_layers": ["AI_PREVIEW_DIM"],
            "entities": [
                {
                    "entity_type": "linear_dimension",
                    "coordinates": {"start": [0, 0], "end": [60, 0]},
                    "dimensions": {"measurement": 60, "offset": 10},
                    "layer": "AI_PREVIEW_DIM",
                    "linetype": "ByLayer",
                    "dimension_source": "explicit_dimension",
                    "confidence": 1,
                }
            ],
        }
    )
    result = PostExecutionVerifier().verify(
        FakeAdapter({"D2": FakeMovedAlignedDimension()}), plan, ["D2"]
    )
    assert not result["passed"]
    row = next(item for item in result["rows"] if item["property"] == "ext_line1_point")
    assert row["target"] == [0, 0]
    assert row["actual"] == [1.0, 0.0, 0.0]


def test_diametric_dimension_compares_both_chord_points():
    """Diametric dimensions must verify both defining chord endpoints."""
    plan = DrawingPlan.model_validate(
        {
            "task_name": "diameter",
            "unit": "mm",
            "user_confirmed": True,
            "existing_layers": ["AI_PREVIEW_DIM"],
            "entities": [
                {
                    "entity_type": "diametric_dimension",
                    "coordinates": {
                        "chord_point": [0, 0],
                        "far_chord_point": [10, 0],
                    },
                    "dimensions": {"measurement": 10},
                    "layer": "AI_PREVIEW_DIM",
                    "linetype": "ByLayer",
                    "dimension_source": "explicit_dimension",
                    "confidence": 1,
                }
            ],
        }
    )
    result = PostExecutionVerifier().verify(
        FakeAdapter({"D1": FakeDiametricDimension()}), plan, ["D1"]
    )
    assert result["passed"], result
    properties = {row["property"] for row in result["rows"]}
    assert {"chord_point", "far_chord_point"} <= properties


def test_centerline_verification_checks_effective_layer_linetype():
    plan = DrawingPlan.model_validate(
        {
            "task_name": "centerline",
            "unit": "mm",
            "user_confirmed": True,
            "existing_layers": ["AI_PREVIEW_CENTER"],
            "entities": [
                {
                    "entity_type": "line",
                    "coordinates": {"start": [0, 0, 0], "end": [100, 0, 0]},
                    "dimensions": {},
                    "layer": "AI_PREVIEW_CENTER",
                    "linetype": "ByLayer",
                    "dimension_source": "explicit_dimension",
                    "confidence": 1,
                }
            ],
        }
    )
    failed = PostExecutionVerifier().verify(
        FakeAdapter({"L1": FakeLine()}, "Continuous"), plan, ["L1"]
    )
    assert not failed["passed"]
    row = next(item for item in failed["rows"] if item["property"] == "effective_linetype")
    assert row["actual"] == "Continuous"

    passed = PostExecutionVerifier().verify(
        FakeAdapter({"L1": FakeLine()}, "CENTER2"), plan, ["L1"]
    )
    assert passed["passed"], passed


@pytest.mark.parametrize(
    ("kind", "observed", "expected_pass"),
    [
        ("polyline", "AcDbPolyline", True),
        ("polyline", "AcDb2dPolyline", True),
        ("polyline", "AcDbSpline", False),
        ("center", "CENTER", True),
        ("center", "CENTER2", True),
        ("center", "CENTERX2", True),
        ("center", "Continuous", False),
        ("hidden", "HIDDEN", True),
        ("hidden", "HIDDEN2", True),
        ("hidden", "HIDDENX2", True),
        ("hidden", "Continuous", False),
        ("linear_dimension", "AcDbRotatedDimension", True),
        ("linear_dimension", "AcDbAlignedDimension", True),
        ("linear_dimension", "AcDbCircle", False),
    ],
)
def test_categorical_membership_has_nullable_error_and_strict_json(kind, observed, expected_pass):
    actual: FakePolyline | FakeAlignedDimension | FakeLine
    if kind == "polyline":
        coordinates = (
            (0, 0, 0, 10, 0, 0, 10, 5, 0) if observed == "AcDb2dPolyline" else (0, 0, 10, 0, 10, 5)
        )
        actual = FakePolyline(coordinates)
        actual.ObjectName = observed
        plan = entity_plan("polyline", {"points": [[0, 0], [10, 0], [10, 5]]}, {"closed": False})
        property_name = "object_type"
        linetype = "Continuous"
    elif kind == "linear_dimension":
        actual = FakeAlignedDimension()
        actual.ObjectName = observed
        plan = entity_plan(
            "linear_dimension",
            {"start": [0, 0], "end": [60, 0]},
            {"measurement": 60, "offset": 10},
            "AI_PREVIEW_DIM",
        )
        property_name = "object_type"
        linetype = "Continuous"
    else:
        actual = FakeLine()
        layer = "AI_PREVIEW_CENTER" if kind == "center" else "AI_PREVIEW_HIDDEN"
        actual.Layer = layer
        plan = entity_plan("line", {"start": [0, 0, 0], "end": [100, 0, 0]}, {}, layer)
        property_name = "effective_linetype"
        linetype = observed
    result = PostExecutionVerifier().verify(
        FakeAdapter({actual.Handle: actual}, linetype), plan, [actual.Handle]
    )
    row = next(item for item in result["rows"] if item["property"] == property_name)
    assert row["actual"] == observed
    assert row["passed"] is result["passed"] is expected_pass
    assert row["error"] is None
    strict_report(result)


@pytest.mark.parametrize(
    ("value", "marker"), [(math.nan, "NaN"), (math.inf, "Infinity"), (-math.inf, "-Infinity")]
)
@pytest.mark.parametrize("axis", [0, 1, 2])
@pytest.mark.parametrize(
    ("attribute", "property_name"), [("StartPoint", "start"), ("EndPoint", "end")]
)
def test_every_nonfinite_xyz_position_fails_without_inventing_coordinates(
    value, marker, axis, attribute, property_name
):
    actual = FakeLine()
    point = list(getattr(actual, attribute))
    point[axis] = value
    setattr(actual, attribute, tuple(point))
    plan = entity_plan("line", {"start": [0, 0, 0], "end": [100, 0, 0]}, {}, "AI_PREVIEW_CENTER")
    result = PostExecutionVerifier().verify(FakeAdapter({"L1": actual}, "CENTER2"), plan, ["L1"])
    row = next(item for item in result["rows"] if item["property"] == property_name)
    assert result["passed"] is row["passed"] is False
    assert row["actual"][axis] == marker
    assert row["error"] is None
    assert result["errors"]
    strict_report(result)


@pytest.mark.parametrize(
    ("value", "marker"), [(math.nan, "NaN"), (math.inf, "Infinity"), (-math.inf, "-Infinity")]
)
@pytest.mark.parametrize("position", range(6))
def test_every_nonfinite_polyline_coordinate_fails_even_after_finite_vertices(
    value, marker, position
):
    coordinates = [0.0, 0.0, 10.0, 0.0, 10.0, 5.0]
    coordinates[position] = value
    actual = FakePolyline(tuple(coordinates))
    plan = entity_plan("polyline", {"points": [[0, 0], [10, 0], [10, 5]]}, {"closed": False})
    result = PostExecutionVerifier().verify(FakeAdapter({"P1": actual}), plan, ["P1"])
    row = next(item for item in result["rows"] if item["property"] == "vertices")
    assert result["passed"] is row["passed"] is False
    assert result["actual_entities"][0]["coordinates"][position] == marker
    assert row["actual"] is row["error"] is None
    strict_report(result)


@pytest.mark.parametrize(
    "value",
    [None, "not-a-number", True, math.nan, math.inf, -math.inf, 10**400, Fraction(10**400), 1e308],
)
def test_invalid_missing_nonfinite_and_overflowing_radius_fail_with_strict_report(value):
    actual = FakeCircle()
    actual.Radius = value
    plan = entity_plan("circle", {"center": [500, 300, 0]}, {"radius": 50})
    result = PostExecutionVerifier().verify(FakeAdapter({"A1": actual}), plan, ["A1"])
    row = next(item for item in result["rows"] if item["property"] == "radius")
    assert result["passed"] is row["passed"] is False
    if value == 1e308:
        assert result["actual_entities"][0]["diameter"] == "Infinity"
        assert row["error"] == 1e308
    strict_report(result)


@pytest.mark.parametrize("attribute", ["StartAngle", "EndAngle"])
@pytest.mark.parametrize(
    "value", [None, "not-an-angle", math.nan, math.inf, -math.inf, 10**400, 1e308]
)
def test_invalid_or_nonfinite_native_angles_never_pass_normalization(attribute, value):
    actual = FakeArc(0, 90)
    setattr(actual, attribute, value)
    plan = entity_plan("arc", {"center": [0, 0]}, {"radius": 5, "start_angle": 0, "end_angle": 90})
    result = PostExecutionVerifier().verify(FakeAdapter({"A2": actual}), plan, ["A2"])
    property_name = "start_angle" if attribute == "StartAngle" else "end_angle"
    row = next(item for item in result["rows"] if item["property"] == property_name)
    assert result["passed"] is row["passed"] is False
    assert row["error"] is None
    strict_report(result)


def test_finite_extreme_vertices_report_overflowed_bounds_as_markers_and_fail():
    actual = FakePolyline((-1e308, -1e308, 1e308, -1e308, 1e308, 1e308))
    plan = entity_plan("polyline", {"points": [[0, 0], [10, 0], [10, 5]]}, {"closed": False})
    result = PostExecutionVerifier().verify(FakeAdapter({"P1": actual}), plan, ["P1"])
    assert result["passed"] is False
    assert result["actual_entities"][0]["width"] == "Infinity"
    assert result["actual_entities"][0]["height"] == "Infinity"
    strict_report(result)


def test_finite_numeric_subtraction_overflow_stays_failed_with_nullable_distance():
    actual = FakeCircle()
    actual.Radius = -1e308
    plan = entity_plan("circle", {"center": [500, 300, 0]}, {"radius": 1e308})
    result = PostExecutionVerifier().verify(FakeAdapter({"A1": actual}), plan, ["A1"])
    row = next(item for item in result["rows"] if item["property"] == "radius")
    assert result["passed"] is row["passed"] is False
    assert row["error"] is None
    assert row["actual"] == -1e308 and row["target"] == 1e308
    strict_report(result)
