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


class FakeDocument:
    """Minimal COM document double."""

    def __init__(self, objects):
        """Store fake entities by handle."""
        self.objects = objects

    def HandleToObject(self, handle):  # noqa: N802 - mirrors AutoCAD COM
        """Return a fake entity by COM-style handle lookup."""
        return self.objects[handle]


class FakeAdapter:
    """Minimal adapter double used by the verifier."""

    def __init__(self, objects):
        """Create the adapter with a fake document."""
        self.document = FakeDocument(objects)

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
    result = PostExecutionVerifier().verify(
        FakeAdapter({"A1": FakeCircle()}), plan, ["A1"]
    )
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
