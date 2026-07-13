"""Unit tests for guarded plan execution without AutoCAD."""

from cad_memory.executor import PlanExecutor
from cad_memory.models import DrawingPlan


class FakeDimension:
    """Dimension whose measured points are independent from text position."""

    Handle = "D7"
    ExtLine1Point = (0.0, 3.5, 0.0)
    ExtLine2Point = (0.0, -3.5, 0.0)
    XLine1Point = (0.0, 3.5, 0.0)
    XLine2Point = (0.0, -3.5, 0.0)
    TextPosition = (0.0, 0.0, 0.0)


class FakeDocument:
    """Small AutoCAD document double with undo-mark support."""

    def __init__(self):
        """Create one fake dimension."""
        self.dimension = FakeDimension()
        self.undo_started = False
        self.undo_ended = False

    def StartUndoMark(self):  # noqa: N802 - mirrors AutoCAD COM
        """Record the start of the undo group."""
        self.undo_started = True

    def EndUndoMark(self):  # noqa: N802 - mirrors AutoCAD COM
        """Record the end of the undo group."""
        self.undo_ended = True

    def HandleToObject(self, handle):  # noqa: N802 - mirrors AutoCAD COM
        """Return the fake dimension by handle."""
        assert handle == "D7"
        return self.dimension


class FakeAdapter:
    """Adapter double for dimension-layout execution."""

    def __init__(self):
        """Create the fake document."""
        self.document = FakeDocument()

    def list_layers(self):
        """Return the layer used by the plan."""
        return ["AI_PREVIEW_DIM"]

    def _get_document(self, operation):
        return self.document

    @staticmethod
    def _to_variant_array(value):
        return value

    @staticmethod
    def refresh_view():
        """Simulate a no-op view refresh."""


def test_dimension_layout_does_not_change_measured_geometry():
    """Move only text while measured points remain byte-for-byte equal."""
    plan = DrawingPlan.model_validate(
        {
            "task_name": "layout",
            "unit": "mm",
            "user_confirmed": True,
            "preview_mode": True,
            "entities": [
                {
                    "entity_type": "aligned_dimension",
                    "coordinates": {"text_position": [20, 5]},
                    "dimensions": {"measurement": 7},
                    "layer": "AI_PREVIEW_DIM",
                    "linetype": "ByLayer",
                    "dimension_source": "explicit_dimension",
                    "confidence": 1,
                    "operation": "layout_only",
                    "target_handles": ["D7"],
                }
            ],
        }
    )
    adapter = FakeAdapter()
    before = (
        adapter.document.dimension.ExtLine1Point,
        adapter.document.dimension.ExtLine2Point,
        adapter.document.dimension.XLine1Point,
        adapter.document.dimension.XLine2Point,
    )
    result = PlanExecutor().execute(adapter, plan)
    after = (
        adapter.document.dimension.ExtLine1Point,
        adapter.document.dimension.ExtLine2Point,
        adapter.document.dimension.XLine1Point,
        adapter.document.dimension.XLine2Point,
    )
    assert result["success"]
    assert before == after
    assert adapter.document.dimension.TextPosition == (20.0, 5.0, 0.0)
    assert adapter.document.undo_started and adapter.document.undo_ended
