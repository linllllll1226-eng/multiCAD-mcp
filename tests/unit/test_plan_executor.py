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


class CreatedEntity:
    """Created entity double with controllable post-create failures."""

    ObjectName = "AcDbLine"

    def __init__(self, handle: str, *, fail_linetype: bool = False, fail_delete: bool = False):
        """Create one line-like object."""
        self.Handle = handle
        self.Layer = "AI_PREVIEW_OUTLINE"
        self._linetype = "ByLayer"
        self.fail_linetype = fail_linetype
        self.fail_delete = fail_delete
        self.deleted = False

    @property
    def Linetype(self):  # noqa: N802 - mirrors AutoCAD COM
        """Return the current line type."""
        return self._linetype

    @Linetype.setter
    def Linetype(self, value):  # noqa: N802 - mirrors AutoCAD COM
        if self.fail_linetype:
            raise RuntimeError(f"refused linetype for {self.Handle}")
        self._linetype = value

    def Delete(self):  # noqa: N802 - mirrors AutoCAD COM
        """Delete the object unless the test explicitly refuses it."""
        if self.fail_delete:
            raise RuntimeError(f"refused delete for {self.Handle}")
        self.deleted = True


class CreationDocument:
    """Document double for creation and rollback diagnostics."""

    Name = "Drawing1.dwg"
    FullName = ""
    Path = ""

    def __init__(self, *, lookup_fail_handles=(), fail_linetype_handles=(), fail_delete_handles=()):
        """Configure failure handles while keeping created objects inspectable."""
        self.objects = {}
        self.lookup_fail_handles = set(lookup_fail_handles)
        self.fail_linetype_handles = set(fail_linetype_handles)
        self.fail_delete_handles = set(fail_delete_handles)
        self.undo_started = False
        self.undo_ended = False

    def StartUndoMark(self):  # noqa: N802 - mirrors AutoCAD COM
        """Record the start of the undo group."""
        self.undo_started = True

    def EndUndoMark(self):  # noqa: N802 - mirrors AutoCAD COM
        """Record the end of the undo group."""
        self.undo_ended = True

    def HandleToObject(self, handle):  # noqa: N802 - mirrors AutoCAD COM
        """Resolve a handle or simulate a lookup failure."""
        if handle in self.lookup_fail_handles:
            raise RuntimeError(f"lookup failed for {handle}")
        return self.objects[handle]


class CreationAdapter:
    """Adapter double that creates line objects and exposes their handles."""

    def __init__(self, **document_options):
        """Create a document with optional failure injection."""
        self.document = CreationDocument(**document_options)
        self.created = []

    def list_layers(self):
        """Return the preview layer accepted by the plan validator."""
        return ["AI_PREVIEW_OUTLINE"]

    def _get_document(self, _operation):
        return self.document

    def draw_line(self, _start, _end, layer, *_args, **_kwargs):
        """Create and index one line-like object."""
        handle = f"L{len(self.created) + 1}"
        entity = CreatedEntity(
            handle,
            fail_linetype=handle in self.document.fail_linetype_handles,
            fail_delete=handle in self.document.fail_delete_handles,
        )
        entity.Layer = layer
        self.created.append(entity)
        self.document.objects[handle] = entity
        return handle

    @staticmethod
    def refresh_view():
        """Simulate a no-op view refresh."""


def _line_plan(linetypes):
    """Build a confirmed line plan for executor failure tests."""
    return DrawingPlan.model_validate(
        {
            "task_name": "atomic-rollback",
            "drawing_profile": "general_2d",
            "unit": "mm",
            "user_confirmed": True,
            "preview_mode": True,
            "entities": [
                {
                    "entity_type": "line",
                    "coordinates": {
                        "start": [index * 10, 0],
                        "end": [index * 10 + 5, 0],
                    },
                    "layer": "AI_PREVIEW_OUTLINE",
                    "linetype": linetype,
                    "dimension_source": "explicit_dimension",
                    "confidence": 1,
                }
                for index, linetype in enumerate(linetypes)
            ],
        }
    )


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


def test_linetype_failure_rolls_back_the_immediately_registered_handle():
    """A post-create line type failure must not leave orphan geometry."""
    adapter = CreationAdapter(fail_linetype_handles={"L1"})
    result = PlanExecutor().execute(adapter, _line_plan(["Continuous"]))

    assert result["success"] is False
    assert "refused linetype for L1" in result["execution_error"]
    assert result["rolled_back"] is True
    assert adapter.created[0].deleted is True
    assert result["rollback_diagnostics"]["details"] == [{"handle": "L1", "status": "deleted"}]


def test_provenance_failure_rolls_back_the_created_entity(monkeypatch):
    """Provenance writes happen after ownership registration and are atomic."""
    adapter = CreationAdapter()

    def fail_provenance(*_args, **_kwargs):
        raise RuntimeError("provenance write failed")

    monkeypatch.setattr("cad_memory.executor.write_entity_provenance", fail_provenance)
    result = PlanExecutor().execute(
        adapter,
        _line_plan(["ByLayer"]),
        task_id="task-1",
        execution_result_id=1,
    )

    assert result["success"] is False
    assert result["results"][0]["error"] == "provenance write failed"
    assert result["rolled_back"] is True
    assert adapter.created[0].deleted is True


def test_rollback_lookup_failure_is_reported_without_claiming_full_rollback():
    """A handle that cannot be looked up is reported as an incomplete rollback."""
    adapter = CreationAdapter(lookup_fail_handles={"L1"})
    result = PlanExecutor().execute(adapter, _line_plan(["ByLayer"]))

    assert result["success"] is False
    assert result["rolled_back"] is False
    assert adapter.created[0].deleted is False
    failure = result["rollback_diagnostics"]["failed"][0]
    assert failure["handle"] == "L1"
    assert failure["stage"] == "lookup"
    assert "lookup failed for L1" in failure["error"]


def test_partial_multi_entity_rollback_reports_each_handle():
    """A failed batch rolls back in reverse order and exposes partial recovery."""
    adapter = CreationAdapter(
        fail_linetype_handles={"L2"},
        fail_delete_handles={"L2"},
    )
    result = PlanExecutor().execute(adapter, _line_plan(["Continuous", "Continuous"]))

    assert result["success"] is False
    assert result["rolled_back"] is False
    assert adapter.created[0].deleted is True
    assert adapter.created[1].deleted is False
    diagnostics = result["rollback_diagnostics"]
    assert diagnostics["attempted"] == ["L1", "L2"]
    assert diagnostics["details"] == [
        {
            "handle": "L2",
            "status": "failed",
            "stage": "delete",
            "error": "refused delete for L2",
        },
        {"handle": "L1", "status": "deleted"},
    ]
