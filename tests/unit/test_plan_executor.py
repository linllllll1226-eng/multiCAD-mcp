"""Unit tests for guarded plan execution without AutoCAD."""

import math
from typing import Any

import pytest

from adapters.mixins.drawing_mixin import DrawingMixin
from cad_memory.executor import PlanExecutor
from cad_memory.models import DrawingPlan
from mcp_tools.constants import COLOR_MAP


class FakeDimension:
    """Dimension whose measured points are independent from text position."""

    Handle = "D7"
    ExtLine1Point = (0.0, 3.5, 0.0)
    ExtLine2Point = (0.0, -3.5, 0.0)
    XLine1Point = (0.0, 3.5, 0.0)
    XLine2Point = (0.0, -3.5, 0.0)
    TextPosition = (0.0, 0.0, 0.0)
    Color = 2


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
        self._color = 7
        self.fail_color = False
        self.creation_arguments: tuple[Any, ...] = ()

    @property
    def Color(self):  # noqa: N802 - mirrors AutoCAD COM
        """Expose an explicit color until the executor requests layer inheritance."""
        return self._color

    @Color.setter
    def Color(self, value):  # noqa: N802 - mirrors AutoCAD COM
        if self.fail_color:
            raise RuntimeError(f"refused color for {self.Handle}")
        self._color = value

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
        self.ModelSpace: Any = None
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


class _CreationModelSpace:
    """Create entities before the drawing mixin applies their properties."""

    def __init__(self, document):
        self.document = document

    def AddLine(self, _start, _end):  # noqa: N802 - mirrors AutoCAD COM
        handle = f"L{len(self.document.objects) + 1}"
        entity = CreatedEntity(handle)
        self.document.objects[handle] = entity
        return entity


class FailingDrawingMixinAdapter(DrawingMixin):
    """Exercise the real adapter path when post-create property work fails."""

    def __init__(self):
        """Create a document whose property finalization always fails."""
        self.document = CreationDocument()
        self.document.ModelSpace = _CreationModelSpace(self.document)

    def list_layers(self):
        return ["AI_PREVIEW_OUTLINE"]

    def _get_document(self, operation: str = "operation"):
        return self.document

    @staticmethod
    def _to_variant_array(value):
        return value

    @staticmethod
    def _apply_properties(_entity, _layer, _color, _lineweight=0):
        raise RuntimeError("adapter property finalization failed")

    @staticmethod
    def _track_entity(_entity, _entity_type):
        raise AssertionError("tracking must not run after property failure")

    @staticmethod
    def refresh_view():
        return True


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
    original_color = adapter.document.dimension.Color
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
    assert adapter.document.dimension.Color == original_color
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


def test_adapter_property_failure_rolls_back_handle_registered_at_add_time():
    """A mixin finalization failure must not strand the already-created CAD object."""
    adapter = FailingDrawingMixinAdapter()
    result = PlanExecutor().execute(adapter, _line_plan(["ByLayer"]))

    assert result["success"] is False
    assert result["execution_error"] == "adapter property finalization failed"
    assert result["rolled_back"] is True
    created = adapter.document.objects["L1"]
    assert created.deleted is True
    assert result["rollback_diagnostics"] == {
        "attempted": ["L1"],
        "details": [{"handle": "L1", "status": "deleted"}],
        "succeeded": ["L1"],
        "failed": [],
        "fully_rolled_back": True,
    }


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


class LayerColorModelSpace:
    """Model space whose new objects initially have an explicit white color."""

    def __init__(self, document, refuse_color):
        """Keep created objects and optional refused color writes inspectable."""
        self.document = document
        self.refuse_color = refuse_color

    def __getattr__(self, name):
        """Expose only the AutoCAD creation methods used by guarded plans."""
        object_types = {
            "AddLine": "AcDbLine",
            "AddText": "AcDbText",
            "AddPolyline": "AcDb2dPolyline",
            "AddCircle": "AcDbCircle",
            "AddArc": "AcDbArc",
            "AddDimAligned": "AcDbAlignedDimension",
            "AddDimDiametric": "AcDbDiametricDimension",
            "AddDimRadial": "AcDbRadialDimension",
        }
        if name not in object_types:
            raise AttributeError(name)

        def create(*arguments):
            handle = f"C{len(self.document.objects) + 1}"
            entity = CreatedEntity(handle)
            entity.ObjectName = object_types[name]
            entity.creation_arguments = arguments
            entity.fail_color = self.refuse_color
            self.document.objects[handle] = entity
            return entity

        return create


class LayerColorAdapter(DrawingMixin):
    """Run the real drawing dispatch with inspectable colored layers and objects."""

    def __init__(self, *, refuse_color=False):
        """Create differently colored preview layers and a model-space double."""
        self.document = CreationDocument()
        self.document.ModelSpace = LayerColorModelSpace(self.document, refuse_color)
        self.layer_colors = {"AI_PREVIEW_DIM": 6, "AI_UNCERTAIN": 30}
        self.requested_colors = []

    def list_layers(self):
        return list(self.layer_colors)

    def _get_document(self, operation: str = "operation"):
        return self.document

    def _apply_properties(self, entity, layer, color, _lineweight=0):
        self.requested_colors.append(color)
        entity.Layer = layer
        try:
            entity.Color = COLOR_MAP[color] if isinstance(color, str) else color
        except RuntimeError:
            # The production utility mixin logs and swallows property errors;
            # the guarded executor must still detect a refused Color setter.
            pass

    @staticmethod
    def _to_variant_array(value):
        return value

    @staticmethod
    def _points_to_variant_array(value):
        return tuple(coordinate for point in value for coordinate in point)

    @staticmethod
    def _to_radians(value):
        return math.radians(value)

    @staticmethod
    def _track_entity(_entity, _entity_type):
        pass

    @staticmethod
    def _validate_connection():
        pass

    @staticmethod
    def refresh_view():
        pass

    def effective_color(self, entity):
        return self.layer_colors[entity.Layer] if entity.Color == 256 else entity.Color


def _color_plan(kind, layer):
    coordinates, dimensions = {
        "line": ({"start": [0, 0], "end": [20, 0]}, {}),
        "text": ({"position": [1, 3]}, {"height": 2.5}),
        "rectangle": ({"corner1": [0, 0], "corner2": [20, 10]}, {}),
        "circle": ({"center": [0, 0]}, {"radius": 5}),
        "arc": ({"center": [0, 0]}, {"radius": 5, "start_angle": 0, "end_angle": 90}),
        "polyline": ({"points": [[0, 0], [20, 0], [20, 10]]}, {"closed": False}),
        "aligned_dimension": ({"start": [0, 0], "end": [20, 0]}, {"offset": 5}),
        "linear_dimension": ({"start": [0, 0], "end": [20, 0]}, {"offset": 5}),
        "diametric_dimension": (
            {"chord_point": [-5, 0], "far_chord_point": [5, 0]},
            {"diameter": 10},
        ),
        "radial_dimension": ({"center": [0, 0], "chord_point": [5, 0]}, {"radius": 5}),
    }[kind]
    return DrawingPlan.model_validate(
        {
            "task_name": "layer-color-preview",
            "unit": "mm",
            "user_confirmed": True,
            "preview_mode": True,
            "entities": [
                {
                    "entity_type": kind,
                    "coordinates": coordinates,
                    "dimensions": dimensions,
                    "layer": layer,
                    "dimension_source": "explicit_dimension",
                    "confidence": 1,
                    "text_override": "PENDING REVIEW" if kind == "text" else "",
                }
            ],
        }
    )


@pytest.mark.parametrize("layer", ["AI_PREVIEW_DIM", "AI_UNCERTAIN"])
@pytest.mark.parametrize(
    "kind",
    [
        "line",
        "text",
        "rectangle",
        "circle",
        "arc",
        "polyline",
        "aligned_dimension",
        "linear_dimension",
        "diametric_dimension",
        "radial_dimension",
    ],
)
def test_created_geometry_and_dimensions_follow_the_actual_layer_color(kind, layer):
    adapter = LayerColorAdapter()
    result = PlanExecutor().execute(adapter, _color_plan(kind, layer))

    assert result["success"], result
    entity = adapter.document.objects[result["handles"][0]]
    assert entity.Color == 256
    assert entity.Layer == layer
    assert adapter.effective_color(entity) == (30 if layer == "AI_UNCERTAIN" else 6)
    if kind not in {"diametric_dimension", "radial_dimension"}:
        assert adapter.requested_colors == ["bylayer"]
    original_geometry = entity.creation_arguments
    adapter.layer_colors[layer] = 1
    assert adapter.effective_color(entity) == 1
    assert entity.creation_arguments == original_geometry


@pytest.mark.parametrize(
    "kind", ["line", "text", "aligned_dimension", "diametric_dimension", "radial_dimension"]
)
def test_refused_color_write_rolls_back_instead_of_reporting_a_styled_preview(kind):
    adapter = LayerColorAdapter(refuse_color=True)
    result = PlanExecutor().execute(adapter, _color_plan(kind, "AI_UNCERTAIN"))

    assert result["success"] is False
    assert result["execution_error"] == "refused color for C1"
    assert result["handles"] == []
    assert result["entity_records"] == []
    assert result["rolled_back"] is True
    assert adapter.document.objects["C1"].deleted is True
    assert result["rollback_diagnostics"]["details"] == [{"handle": "C1", "status": "deleted"}]
