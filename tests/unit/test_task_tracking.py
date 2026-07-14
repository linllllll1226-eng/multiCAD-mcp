"""Unit tests for task-scoped CAD ownership, commit, and reversible revert."""

from __future__ import annotations

from typing import Any

import pytest

from cad_memory.database import SQLiteMemoryStore
from cad_memory.executor import PlanExecutor
from cad_memory.models import DrawingPlan
from cad_memory.provenance import (
    build_entity_provenance,
    generate_task_id,
    read_entity_provenance,
    write_entity_provenance,
)
from cad_memory.task_manager import REVERT_LAYER, TaskTrackingManager
from mcp_tools.tools.validation import _rollback_task_handles


class FakeLayer:
    """Small layer double."""

    def __init__(self, name: str):
        """Create a named visible layer."""
        self.Name = name
        self.LayerOn = True
        self.Color = 7
        self.Linetype = "Continuous"


class FakeLayers:
    """Case-insensitive AutoCAD Layers collection double."""

    def __init__(self, names: list[str]):
        """Create the supplied layer names."""
        self.items = {name.upper(): FakeLayer(name) for name in names}

    def Item(self, name: str):  # noqa: N802 - AutoCAD COM spelling
        if name.upper() not in self.items:
            raise KeyError(name)
        return self.items[name.upper()]

    def Add(self, name: str):  # noqa: N802 - AutoCAD COM spelling
        layer = FakeLayer(name)
        self.items[name.upper()] = layer
        return layer


class FakeApplications:
    """RegisteredApplications double used by XData."""

    def __init__(self):
        """Start with no registered applications."""
        self.names: set[str] = set()

    def Item(self, name: str):  # noqa: N802 - AutoCAD COM spelling
        if name not in self.names:
            raise KeyError(name)
        return name

    def Add(self, name: str):  # noqa: N802 - AutoCAD COM spelling
        self.names.add(name)
        return name


class FakeEntity:
    """Circle-like task-owned entity with persistent XData storage."""

    ObjectName = "AcDbCircle"
    Linetype = "ByLayer"
    Center = (0.0, 0.0, 0.0)
    Radius = 5.0

    def __init__(self, handle: str, layer: str, *, fail_target: str = ""):
        """Create a circle-like entity on one layer."""
        self.Handle = handle
        self._layer = layer
        self.fail_target = fail_target
        self.xdata: tuple[list[int], list[Any]] | None = None
        self.deleted = False

    @property
    def Layer(self):  # noqa: N802 - AutoCAD COM spelling
        return self._layer

    @Layer.setter
    def Layer(self, value):  # noqa: N802 - AutoCAD COM spelling
        if value == self.fail_target:
            raise RuntimeError(f"refused layer {value}")
        self._layer = value

    def SetXData(self, codes, values):  # noqa: N802 - AutoCAD COM spelling
        self.xdata = (list(codes), list(values))

    def GetXData(self, _application):  # noqa: N802 - AutoCAD COM spelling
        return self.xdata

    def Delete(self):  # noqa: N802 - AutoCAD COM spelling
        self.deleted = True


class FakeDocument:
    """Active document double with handles and undo marks."""

    Name = "Drawing1.dwg"
    FullName = ""
    Path = ""

    def __init__(self, entities: list[FakeEntity]):
        """Index entities by handle and provide standard layers."""
        self.objects = {entity.Handle: entity for entity in entities}
        self.Layers = FakeLayers(
            [
                "AI_PREVIEW_OUTLINE",
                "AI_UNCERTAIN",
                "OUTLINE",
                "CENTER",
                "HIDDEN",
                "HATCH",
                "DIM",
            ]
        )
        self.RegisteredApplications = FakeApplications()
        self.undo_started = 0
        self.undo_ended = 0

    def HandleToObject(self, handle):  # noqa: N802 - AutoCAD COM spelling
        return self.objects[handle]

    def StartUndoMark(self):  # noqa: N802 - AutoCAD COM spelling
        self.undo_started += 1

    def EndUndoMark(self):  # noqa: N802 - AutoCAD COM spelling
        self.undo_ended += 1


class FakeAdapter:
    """Adapter double for task operations and one circle creation."""

    def __init__(self, document: FakeDocument):
        """Attach one fake active document."""
        self.document = document

    def _get_document(self, _operation):
        return self.document

    def list_layers(self):
        return [layer.Name for layer in self.document.Layers.items.values()]

    @staticmethod
    def _int_array_to_variant(value):
        return value

    @staticmethod
    def _mixed_array_to_variant(value):
        return value

    def draw_circle(self, center, radius, layer, *_args, **_kwargs):
        handle = f"C{len(self.document.objects) + 1}"
        entity = FakeEntity(handle, layer)
        entity.Center = center
        entity.Radius = radius
        self.document.objects[handle] = entity
        return handle

    @staticmethod
    def refresh_view():
        return None


def _metadata(task_id: str, *, approximate: bool = False) -> dict[str, Any]:
    return build_entity_provenance(
        task_id=task_id,
        execution_result_id=1,
        drawing_profile="general_2d",
        source_type=(
            "approximate_reference" if approximate else "explicit_dimension"
        ),
        confidence=0.5 if approximate else 1.0,
        approximate_reference=approximate,
    )


def _seed_task(
    store: SQLiteMemoryStore,
    adapter: FakeAdapter,
    task_id: str,
    entity: FakeEntity,
    *,
    status: str = "verified",
    approximate: bool = False,
) -> None:
    metadata = _metadata(task_id, approximate=approximate)
    write_entity_provenance(adapter, adapter.document, entity, metadata)
    store.create_ai_task(
        task_id=task_id,
        task_name=task_id,
        drawing_name="Drawing1.dwg",
        drawing_full_name="",
        drawing_profile="general_2d",
        status=status,
        execution_result_id=1,
        plan_data={"task_name": task_id},
    )
    store.add_ai_task_entities(
        task_id,
        [
            {
                "handle": entity.Handle,
                "object_type": entity.ObjectName,
                "operation": "create",
                "owned": True,
                "preview_layer": entity.Layer,
                "current_layer": entity.Layer,
                "formal_layer": "",
                "source_type": metadata["source_type"],
                "confidence": metadata["confidence"],
                "approximate_reference": approximate,
                "metadata": metadata,
            }
        ],
    )


def test_executor_assigns_unique_task_provenance():
    document = FakeDocument([])
    adapter = FakeAdapter(document)
    plan = DrawingPlan.model_validate(
        {
            "task_name": "circle",
            "drawing_profile": "general_2d",
            "unit": "mm",
            "user_confirmed": True,
            "entities": [
                {
                    "entity_type": "circle",
                    "coordinates": {"center": [0, 0, 0]},
                    "dimensions": {"radius": 5},
                    "layer": "AI_PREVIEW_OUTLINE",
                    "linetype": "ByLayer",
                    "dimension_source": "explicit_dimension",
                    "confidence": 1,
                }
            ],
        }
    )
    first_id = generate_task_id()
    second_id = generate_task_id()
    assert first_id != second_id
    result = PlanExecutor().execute(
        adapter, plan, task_id=first_id, execution_result_id=9
    )
    assert result["success"]
    metadata = read_entity_provenance(document.HandleToObject(result["handles"][0]))
    assert metadata["task_id"] == first_id
    assert metadata["drawing_profile"] == "general_2d"


def test_commit_requires_verified_task_and_changes_only_layer(tmp_path):
    entity = FakeEntity("A1", "AI_PREVIEW_OUTLINE")
    adapter = FakeAdapter(FakeDocument([entity]))
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    _seed_task(store, adapter, "task-a", entity, status="executed")
    manager = TaskTrackingManager(store)
    blocked = manager.commit_preview_task(adapter, "task-a", confirmed=True)
    assert blocked["blocked"]
    store.update_ai_task("task-a", status="verified")
    before = (entity.Center, entity.Radius)
    preview = manager.commit_preview_task(adapter, "task-a", confirmed=False)
    assert preview["requires_confirmation"]
    assert entity.Layer == "AI_PREVIEW_OUTLINE"
    result = manager.commit_preview_task(adapter, "task-a", confirmed=True)
    assert result["success"] and result["geometry_unchanged"]
    assert entity.Layer == "OUTLINE"
    assert (entity.Center, entity.Radius) == before
    assert store.get_ai_task("task-a")["status"] == "committed"


def test_approximate_reference_never_enters_formal_layer(tmp_path):
    entity = FakeEntity("U1", "AI_UNCERTAIN")
    adapter = FakeAdapter(FakeDocument([entity]))
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    _seed_task(store, adapter, "task-u", entity, approximate=True)
    with pytest.raises(PermissionError):
        TaskTrackingManager(store).commit_preview_task(
            adapter,
            "task-u",
            layer_mapping={"AI_UNCERTAIN": "OUTLINE"},
            confirmed=True,
        )
    assert entity.Layer == "AI_UNCERTAIN"


def test_revert_is_task_scoped_and_does_not_touch_user_entity(tmp_path):
    task_a_entity = FakeEntity("A1", "AI_PREVIEW_OUTLINE")
    task_b_entity = FakeEntity("B1", "AI_PREVIEW_OUTLINE")
    user_entity = FakeEntity("USER", "OUTLINE")
    document = FakeDocument([task_a_entity, task_b_entity, user_entity])
    adapter = FakeAdapter(document)
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    _seed_task(store, adapter, "task-a", task_a_entity)
    _seed_task(store, adapter, "task-b", task_b_entity)
    manager = TaskTrackingManager(store)
    preview = manager.revert_task(adapter, "task-a", confirmed=False)
    assert preview["requires_confirmation"] and not preview["hard_delete"]
    result = manager.revert_task(adapter, "task-a", confirmed=True)
    assert result["success"] and not result["hard_delete"]
    assert task_a_entity.Layer == REVERT_LAYER
    assert task_b_entity.Layer == "AI_PREVIEW_OUTLINE"
    assert user_entity.Layer == "OUTLINE"
    assert document.Layers.Item(REVERT_LAYER).LayerOn is False
    assert store.get_ai_task("task-a")["status"] == "reverted"
    assert store.get_ai_task("task-b")["status"] == "verified"


def test_committed_revert_requires_extra_confirmation(tmp_path):
    entity = FakeEntity("A1", "OUTLINE")
    adapter = FakeAdapter(FakeDocument([entity]))
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    _seed_task(store, adapter, "task-a", entity, status="committed")
    result = TaskTrackingManager(store).revert_task(
        adapter, "task-a", confirmed=True
    )
    assert result["requires_extra_confirmation"]
    assert entity.Layer == "OUTLINE"


def test_failed_verified_task_can_still_be_safely_reverted(tmp_path):
    entity = FakeEntity("A1", "AI_PREVIEW_OUTLINE")
    adapter = FakeAdapter(FakeDocument([entity]))
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    _seed_task(store, adapter, "task-a", entity, status="failed")
    result = TaskTrackingManager(store).revert_task(
        adapter, "task-a", confirmed=True
    )
    assert result["success"]
    assert entity.Layer == REVERT_LAYER


def test_commit_failure_restores_previously_changed_entities(tmp_path):
    first = FakeEntity("A1", "AI_PREVIEW_OUTLINE")
    second = FakeEntity("A2", "AI_PREVIEW_OUTLINE", fail_target="OUTLINE")
    adapter = FakeAdapter(FakeDocument([first, second]))
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    _seed_task(store, adapter, "task-a", first)
    metadata = _metadata("task-a")
    write_entity_provenance(adapter, adapter.document, second, metadata)
    store.add_ai_task_entities(
        "task-a",
        [
            {
                "handle": "A2",
                "object_type": second.ObjectName,
                "operation": "create",
                "owned": True,
                "preview_layer": second.Layer,
                "current_layer": second.Layer,
                "source_type": "explicit_dimension",
                "confidence": 1,
                "metadata": metadata,
            }
        ],
    )
    with pytest.raises(RuntimeError, match="refused layer"):
        TaskTrackingManager(store).commit_preview_task(
            adapter, "task-a", confirmed=True
        )
    assert first.Layer == "AI_PREVIEW_OUTLINE"
    assert second.Layer == "AI_PREVIEW_OUTLINE"
    assert store.get_ai_task("task-a")["status"] == "verified"


def test_database_reopen_preserves_task_and_entity_metadata(tmp_path):
    path = tmp_path / "memory.db"
    entity = FakeEntity("A1", "AI_PREVIEW_OUTLINE")
    adapter = FakeAdapter(FakeDocument([entity]))
    store = SQLiteMemoryStore(path)
    _seed_task(store, adapter, "task-a", entity)
    reopened = SQLiteMemoryStore(path)
    task = reopened.get_ai_task("task-a")
    assert task["status"] == "verified"
    assert task["entities"][0]["metadata"]["task_id"] == "task-a"
    assert read_entity_provenance(entity)["task_id"] == "task-a"


def test_persistence_failure_rollback_deletes_only_proven_task_objects():
    owned = FakeEntity("A1", "AI_PREVIEW_OUTLINE")
    user = FakeEntity("USER", "OUTLINE")
    adapter = FakeAdapter(FakeDocument([owned, user]))
    write_entity_provenance(adapter, adapter.document, owned, _metadata("task-a"))
    result = _rollback_task_handles(adapter, "task-a", ["A1", "USER"])
    assert result is False
    assert owned.deleted is True
    assert user.deleted is False
