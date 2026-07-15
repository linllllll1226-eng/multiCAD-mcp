"""Unit tests for actual-object verification without AutoCAD."""

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
