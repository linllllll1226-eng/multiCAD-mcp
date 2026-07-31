"""Unit tests for actual-object verification without AutoCAD."""

import math

from cad_memory.models import DrawingPlan
from cad_memory.verifier import PostExecutionVerifier, read_entity_state


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
    result = PostExecutionVerifier().verify(
        FakeAdapter({"A2": FakeArc(0, 180)}), plan, ["A2"]
    )
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
    result = PostExecutionVerifier().verify(
        FakeAdapter({"A2": FakeArc(360, 450)}), plan, ["A2"]
    )
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
