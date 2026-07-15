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

    def __init__(
        self,
        entities: list[FakeEntity],
        *,
        name: str = "Drawing1.dwg",
        full_name: str = "",
    ):
        """Index entities by handle and provide standard layers."""
        self.Name = name
        self.FullName = full_name
        self.Path = full_name.rsplit("\\", 1)[0] if "\\" in full_name else ""
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


class FailingTaskStore(SQLiteMemoryStore):
    """Store double that fails the final task/entity transaction."""

    def update_task_entities_and_status(self, *args, **kwargs):
        """Simulate a SQLite commit failure after CAD objects changed."""
        raise RuntimeError("simulated database failure")


def _metadata(
    task_id: str,
    *,
    approximate: bool = False,
    drawing_name: str = "Drawing1.dwg",
    drawing_full_name: str = "",
) -> dict[str, Any]:
    return build_entity_provenance(
        task_id=task_id,
        execution_result_id=1,
        drawing_profile="general_2d",
        source_type=("approximate_reference" if approximate else "explicit_dimension"),
        confidence=0.5 if approximate else 1.0,
        approximate_reference=approximate,
        drawing_name=drawing_name,
        drawing_full_name=drawing_full_name,
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
    metadata = _metadata(
        task_id,
        approximate=approximate,
        drawing_name=adapter.document.Name,
        drawing_full_name=adapter.document.FullName,
    )
    write_entity_provenance(adapter, adapter.document, entity, metadata)
    store.create_ai_task(
        task_id=task_id,
        task_name=task_id,
        drawing_name=adapter.document.Name,
        drawing_full_name=adapter.document.FullName,
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
    result = PlanExecutor().execute(adapter, plan, task_id=first_id, execution_result_id=9)
    assert result["success"]
    metadata = read_entity_provenance(document.HandleToObject(result["handles"][0]))
    assert metadata["task_id"] == first_id
    assert metadata["drawing_profile"] == "general_2d"
    assert metadata["drawing_name"] == "Drawing1.dwg"


def test_long_xdata_payload_round_trips_across_chunks():
    entity = FakeEntity("A1", "AI_PREVIEW_OUTLINE")
    document = FakeDocument([entity])
    adapter = FakeAdapter(document)
    metadata = build_entity_provenance(
        task_id="task-long",
        execution_result_id=1,
        drawing_profile="profile-" + ("x" * 900),
        source_type="explicit_dimension",
        confidence=1,
        approximate_reference=False,
        drawing_name=document.Name,
    )
    write_entity_provenance(adapter, document, entity, metadata)
    assert entity.xdata is not None
    assert len(entity.xdata[1]) > 3
    assert read_entity_provenance(entity) == metadata


def test_cross_drawing_commit_and_revert_are_blocked(tmp_path):
    original = FakeEntity("A1", "AI_PREVIEW_OUTLINE")
    original_document = FakeDocument(
        [original],
        name="part-a.dwg",
        full_name=r"D:\drawings\part-a.dwg",
    )
    original_adapter = FakeAdapter(original_document)
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    _seed_task(store, original_adapter, "task-a", original)

    copied = FakeEntity("A1", "AI_PREVIEW_OUTLINE")
    copied.xdata = original.xdata
    other_adapter = FakeAdapter(
        FakeDocument(
            [copied],
            name="part-b.dwg",
            full_name=r"D:\drawings\part-b.dwg",
        )
    )
    manager = TaskTrackingManager(store)
    with pytest.raises(PermissionError, match="different drawing"):
        manager.commit_preview_task(other_adapter, "task-a", confirmed=True)
    with pytest.raises(PermissionError, match="different drawing"):
        manager.revert_task(other_adapter, "task-a", confirmed=True)
    assert copied.Layer == "AI_PREVIEW_OUTLINE"
    assert store.get_ai_task("task-a")["status"] == "verified"


def test_same_saved_drawing_path_is_case_insensitive(tmp_path):
    entity = FakeEntity("A1", "AI_PREVIEW_OUTLINE")
    adapter = FakeAdapter(
        FakeDocument(
            [entity],
            name="PART-A.DWG",
            full_name=r"d:\DRAWINGS\PART-A.DWG",
        )
    )
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    _seed_task(store, adapter, "task-a", entity)
    adapter.document.FullName = r"D:\drawings\part-a.dwg"
    adapter.document.Name = "part-a.dwg"
    result = TaskTrackingManager(store).commit_preview_task(adapter, "task-a", confirmed=True)
    assert result["success"]


def test_entity_provenance_from_other_drawing_is_blocked(tmp_path):
    entity = FakeEntity("A1", "AI_PREVIEW_OUTLINE")
    adapter = FakeAdapter(
        FakeDocument(
            [entity],
            name="part-a.dwg",
            full_name=r"D:\drawings\part-a.dwg",
        )
    )
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    _seed_task(store, adapter, "task-a", entity)
    foreign_metadata = _metadata(
        "task-a",
        drawing_name="part-b.dwg",
        drawing_full_name=r"D:\drawings\part-b.dwg",
    )
    write_entity_provenance(adapter, adapter.document, entity, foreign_metadata)
    with pytest.raises(PermissionError, match="provenance belongs"):
        TaskTrackingManager(store).commit_preview_task(adapter, "task-a", confirmed=True)
    assert entity.Layer == "AI_PREVIEW_OUTLINE"


def test_task_list_marks_other_drawing_without_resolving_handles(tmp_path):
    entity = FakeEntity("A1", "AI_PREVIEW_OUTLINE")
    source = FakeAdapter(
        FakeDocument(
            [entity],
            name="part-a.dwg",
            full_name=r"D:\drawings\part-a.dwg",
        )
    )
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    _seed_task(store, source, "task-a", entity)
    other = FakeAdapter(
        FakeDocument(
            [],
            name="part-b.dwg",
            full_name=r"D:\drawings\part-b.dwg",
        )
    )
    task = TaskTrackingManager(store).list_tasks(other)["tasks"][0]
    assert task["active_drawing_match"] is False
    assert task["active_entity_count"] == 0


def test_task_queries_default_to_summaries_and_paginate_entities(tmp_path):
    entities = [FakeEntity(f"A{index}", "AI_PREVIEW_OUTLINE") for index in range(1, 4)]
    adapter = FakeAdapter(FakeDocument(entities))
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    _seed_task(store, adapter, "task-a", entities[0])
    extra_rows = []
    for entity in entities[1:]:
        metadata = _metadata("task-a")
        write_entity_provenance(adapter, adapter.document, entity, metadata)
        extra_rows.append(
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
                "approximate_reference": False,
                "metadata": metadata,
            }
        )
    store.add_ai_task_entities("task-a", extra_rows)

    manager = TaskTrackingManager(store)
    task_page = manager.list_tasks(adapter)
    task = task_page["tasks"][0]
    assert "plan_data" not in task
    assert "verification_data" not in task
    assert task["recorded_entity_count"] == 3
    assert task["active_entity_count"] is None

    counted = manager.list_tasks(adapter, include_active_counts=True)["tasks"][0]
    assert counted["active_entity_count"] == 3

    first = manager.get_task_entities(adapter, "task-a", limit=2)
    assert [row["handle"] for row in first["entities"]] == ["A1", "A2"]
    assert first["has_more"] is True
    assert first["next_offset"] == 2
    assert "plan_data" not in first["task"]
    assert "provenance" not in first["entities"][0]

    second = manager.get_task_entities(adapter, "task-a", offset=2, limit=2)
    assert [row["handle"] for row in second["entities"]] == ["A3"]
    assert second["has_more"] is False


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


def test_commit_manifest_reports_missing_layers_without_modifying_entities(tmp_path):
    entity = FakeEntity("A1", "AI_PREVIEW_OUTLINE")
    document = FakeDocument([entity])
    del document.Layers.items["OUTLINE"]
    adapter = FakeAdapter(document)
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    _seed_task(store, adapter, "task-a", entity, status="verified")
    manager = TaskTrackingManager(store)

    preview = manager.commit_preview_task(adapter, "task-a", confirmed=False)
    assert preview["requires_confirmation"]
    assert preview["missing_layers"] == ["OUTLINE"]
    assert preview["ready_to_commit"] is False
    assert entity.Layer == "AI_PREVIEW_OUTLINE"

    blocked = manager.commit_preview_task(adapter, "task-a", confirmed=True)
    assert blocked["blocked"]
    assert blocked["missing_layers"] == ["OUTLINE"]
    assert entity.Layer == "AI_PREVIEW_OUTLINE"


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
    result = TaskTrackingManager(store).revert_task(adapter, "task-a", confirmed=True)
    assert result["requires_extra_confirmation"]
    assert entity.Layer == "OUTLINE"


def test_committed_revert_with_extra_confirmation_is_idempotent(tmp_path):
    entity = FakeEntity("A1", "OUTLINE")
    adapter = FakeAdapter(FakeDocument([entity]))
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    _seed_task(store, adapter, "task-a", entity, status="committed")
    manager = TaskTrackingManager(store)
    result = manager.revert_task(
        adapter,
        "task-a",
        confirmed=True,
        allow_committed=True,
    )
    repeated = manager.revert_task(
        adapter,
        "task-a",
        confirmed=True,
        allow_committed=True,
    )
    assert result["success"] and entity.Layer == REVERT_LAYER
    assert repeated["already_reverted"] is True


def test_repeated_commit_is_idempotent(tmp_path):
    entity = FakeEntity("A1", "AI_PREVIEW_OUTLINE")
    adapter = FakeAdapter(FakeDocument([entity]))
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    _seed_task(store, adapter, "task-a", entity)
    manager = TaskTrackingManager(store)
    first = manager.commit_preview_task(adapter, "task-a", confirmed=True)
    repeated = manager.commit_preview_task(adapter, "task-a", confirmed=True)
    assert first["success"] and entity.Layer == "OUTLINE"
    assert repeated["already_committed"] is True


def test_failed_verified_task_can_still_be_safely_reverted(tmp_path):
    entity = FakeEntity("A1", "AI_PREVIEW_OUTLINE")
    adapter = FakeAdapter(FakeDocument([entity]))
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    _seed_task(store, adapter, "task-a", entity, status="failed")
    result = TaskTrackingManager(store).revert_task(adapter, "task-a", confirmed=True)
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
        TaskTrackingManager(store).commit_preview_task(adapter, "task-a", confirmed=True)
    assert first.Layer == "AI_PREVIEW_OUTLINE"
    assert second.Layer == "AI_PREVIEW_OUTLINE"
    assert store.get_ai_task("task-a")["status"] == "verified"


def test_database_failure_restores_cad_layer_and_xdata(tmp_path):
    entity = FakeEntity("A1", "AI_PREVIEW_OUTLINE")
    adapter = FakeAdapter(FakeDocument([entity]))
    store = FailingTaskStore(tmp_path / "memory.db")
    _seed_task(store, adapter, "task-a", entity)
    before_metadata = read_entity_provenance(entity)
    with pytest.raises(RuntimeError, match="simulated database failure"):
        TaskTrackingManager(store).commit_preview_task(adapter, "task-a", confirmed=True)
    assert entity.Layer == "AI_PREVIEW_OUTLINE"
    assert read_entity_provenance(entity) == before_metadata
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
