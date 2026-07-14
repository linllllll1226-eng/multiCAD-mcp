"""Task-scoped commit, provenance inspection, and reversible CAD reverts."""

from __future__ import annotations

import ntpath
from typing import Any

from .database import SQLiteMemoryStore
from .executor import undo_group
from .provenance import (
    document_identity,
    read_entity_provenance,
    utc_now,
    write_entity_provenance,
)
from .verifier import read_entity_state

DEFAULT_FORMAL_LAYER_MAP = {
    "AI_PREVIEW_OUTLINE": "OUTLINE",
    "AI_PREVIEW_CENTER": "CENTER",
    "AI_PREVIEW_HIDDEN": "HIDDEN",
    "AI_PREVIEW_HATCH": "HATCH",
    "AI_PREVIEW_DIM": "DIM",
    "AI_UNCERTAIN": "AI_UNCERTAIN",
}
REVERT_LAYER = "AI_REVERTED"


def _geometry_signature(entity: Any) -> dict[str, Any]:
    state = read_entity_state(entity)
    state.pop("layer", None)
    state.pop("handle", None)
    return state


def _get_layer(document: Any, name: str) -> Any:
    try:
        return document.Layers.Item(name)
    except Exception as exc:
        raise ValueError(f"Required layer does not exist: {name}") from exc


def _ensure_revert_layer(document: Any) -> Any:
    try:
        layer = document.Layers.Item(REVERT_LAYER)
    except Exception:
        layer = document.Layers.Add(REVERT_LAYER)
    try:
        layer.Color = 8
    except Exception:
        pass
    try:
        layer.Linetype = "Continuous"
    except Exception:
        pass
    return layer


def _normalized_windows_path(value: str) -> str:
    return ntpath.normcase(ntpath.normpath(str(value or "").strip()))


def _identity_matches(record: dict[str, Any], document: Any) -> bool:
    """Require a task/entity identity to match the active AutoCAD document."""
    current = document_identity(document)
    recorded_full = str(record.get("drawing_full_name") or "").strip()
    current_full = str(current.get("drawing_full_name") or "").strip()
    if recorded_full:
        return bool(current_full) and _normalized_windows_path(
            recorded_full
        ) == _normalized_windows_path(current_full)
    recorded_name = str(record.get("drawing_name") or "").strip()
    current_name = str(current.get("drawing_name") or "").strip()
    return not recorded_name or recorded_name.casefold() == current_name.casefold()


def _assert_document_identity(
    record: dict[str, Any], document: Any, *, source: str
) -> None:
    if not _identity_matches(record, document):
        current = document_identity(document)
        recorded_label = record.get("drawing_full_name") or record.get(
            "drawing_name"
        )
        active_label = current["drawing_full_name"] or current["drawing_name"]
        raise PermissionError(
            f"{source} belongs to a different drawing: "
            f"recorded={recorded_label!r}, active={active_label!r}"
        )


class TaskTrackingManager:
    """Operate only on entities carrying a matching assistant task identifier."""

    def __init__(self, store: SQLiteMemoryStore) -> None:
        """Use the supplied local task store."""
        self.store = store

    def list_tasks(
        self, adapter: Any, *, status: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        """List database tasks and report how many entities still exist in CAD."""
        document = adapter._get_document("cad_list_ai_tasks")
        tasks = self.store.list_ai_tasks(status=status, limit=limit)
        for task in tasks:
            active = 0
            drawing_match = _identity_matches(task, document)
            if drawing_match:
                for row in self.store.get_ai_task_entities(task["task_id"]):
                    try:
                        entity = document.HandleToObject(row["handle"])
                        metadata = read_entity_provenance(entity)
                        if metadata and metadata.get("task_id") == task["task_id"]:
                            active += 1
                    except Exception:
                        pass
            task["active_drawing_match"] = drawing_match
            task["recorded_entity_count"] = len(
                self.store.get_ai_task_entities(task["task_id"])
            )
            task["active_entity_count"] = active
        return {"count": len(tasks), "tasks": tasks}

    def get_task_entities(self, adapter: Any, task_id: str) -> dict[str, Any]:
        """Read only active entities whose XData proves task ownership."""
        task = self.store.get_ai_task(task_id)
        document = adapter._get_document("cad_get_task_entities")
        _assert_document_identity(task, document, source=f"Task {task_id}")
        entities = []
        missing = []
        for row in task["entities"]:
            try:
                entity = document.HandleToObject(row["handle"])
                metadata = read_entity_provenance(entity)
                if row["owned"] and (
                    not metadata or metadata.get("task_id") != task_id
                ):
                    raise PermissionError("Entity provenance does not match task")
                entities.append(
                    {
                        **row,
                        "actual": read_entity_state(entity),
                        "provenance": metadata,
                    }
                )
            except Exception as exc:
                missing.append({"handle": row["handle"], "error": str(exc)})
        return {"task": task, "entities": entities, "missing": missing}

    def get_entity_provenance(self, adapter: Any, handle: str) -> dict[str, Any]:
        """Return XData and actual state for one active drawing handle."""
        document = adapter._get_document("cad_get_entity_provenance")
        entity = document.HandleToObject(handle)
        return {
            "handle": handle,
            "provenance": read_entity_provenance(entity),
            "actual": read_entity_state(entity),
        }

    def commit_preview_task(
        self,
        adapter: Any,
        task_id: str,
        *,
        layer_mapping: dict[str, str] | None = None,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """Move one verified task to formal layers without changing geometry."""
        task = self.store.get_ai_task(task_id)
        if task["status"] == "committed":
            return {"success": True, "already_committed": True, "task_id": task_id}
        if task["status"] != "verified":
            return {
                "success": False,
                "blocked": True,
                "reason": "Only a verified preview task can be committed",
                "task_status": task["status"],
            }
        document = adapter._get_document("cad_commit_preview_task")
        _assert_document_identity(task, document, source=f"Task {task_id}")
        mapping = {**DEFAULT_FORMAL_LAYER_MAP, **(layer_mapping or {})}
        owned = self._load_owned_entities(document, task)
        manifest = []
        for row, entity, metadata in owned:
            source_layer = str(entity.Layer)
            target_layer = mapping.get(source_layer)
            if not target_layer:
                raise ValueError(f"No formal layer mapping for {source_layer}")
            if row["approximate_reference"] and target_layer != "AI_UNCERTAIN":
                raise PermissionError(
                    "Approximate reference geometry cannot enter a formal layer"
                )
            _get_layer(document, target_layer)
            manifest.append(
                {
                    "handle": row["handle"],
                    "object_type": row["object_type"],
                    "source_layer": source_layer,
                    "target_layer": target_layer,
                    "approximate_reference": row["approximate_reference"],
                }
            )
        if not confirmed:
            return {
                "success": False,
                "requires_confirmation": True,
                "task_id": task_id,
                "object_count": len(manifest),
                "objects": manifest,
            }
        snapshots = self._snapshot(owned)
        changed: list[tuple[dict[str, Any], Any, dict[str, Any]]] = []
        try:
            with undo_group(adapter):
                for row, entity, metadata in owned:
                    target_layer = mapping[str(entity.Layer)]
                    original = dict(metadata)
                    entity.Layer = target_layer
                    updated = {
                        **metadata,
                        "status": "committed",
                        "formal_layer": target_layer,
                        "committed_at": utc_now(),
                    }
                    write_entity_provenance(adapter, document, entity, updated)
                    changed.append((row, entity, original))
            self._assert_geometry_unchanged(owned, snapshots)
            entity_updates = []
            for row, entity, _metadata in changed:
                entity_updates.append(
                    {
                        "handle": row["handle"],
                        "current_layer": str(entity.Layer),
                        "formal_layer": str(entity.Layer),
                        "metadata": read_entity_provenance(entity) or {},
                    }
                )
            self.store.update_task_entities_and_status(
                task_id,
                entity_updates=entity_updates,
                status="committed",
            )
        except Exception:
            self._restore_entities(adapter, document, changed, snapshots)
            raise
        return {
            "success": True,
            "task_id": task_id,
            "object_count": len(changed),
            "objects": manifest,
            "geometry_unchanged": True,
        }

    def revert_task(
        self,
        adapter: Any,
        task_id: str,
        *,
        confirmed: bool = False,
        allow_committed: bool = False,
    ) -> dict[str, Any]:
        """Reversibly isolate one task on a hidden layer without global undo."""
        task = self.store.get_ai_task(task_id)
        if task["status"] == "reverted":
            return {"success": True, "already_reverted": True, "task_id": task_id}
        if task["status"] not in {"executed", "verified", "committed", "failed"}:
            return {
                "success": False,
                "blocked": True,
                "reason": "Task is not in a revertible state",
                "task_status": task["status"],
            }
        if task["status"] == "committed" and not allow_committed:
            return {
                "success": False,
                "requires_extra_confirmation": True,
                "reason": "Committed tasks require allow_committed=true",
            }
        document = adapter._get_document("cad_revert_ai_task")
        _assert_document_identity(task, document, source=f"Task {task_id}")
        owned = self._load_owned_entities(document, task)
        manifest = [
            {
                "handle": row["handle"],
                "object_type": row["object_type"],
                "current_layer": str(entity.Layer),
                "action": f"move_to_hidden_{REVERT_LAYER}",
            }
            for row, entity, _metadata in owned
        ]
        if not confirmed:
            return {
                "success": False,
                "requires_confirmation": True,
                "task_id": task_id,
                "object_count": len(manifest),
                "objects": manifest,
                "hard_delete": False,
            }
        revert_layer = _ensure_revert_layer(document)
        previous_layer_on = bool(getattr(revert_layer, "LayerOn", True))
        snapshots = self._snapshot(owned)
        changed: list[tuple[dict[str, Any], Any, dict[str, Any]]] = []
        try:
            with undo_group(adapter):
                for row, entity, metadata in owned:
                    original = dict(metadata)
                    source_layer = str(entity.Layer)
                    entity.Layer = REVERT_LAYER
                    updated = {
                        **metadata,
                        "status": "reverted",
                        "revert_from_layer": source_layer,
                        "reverted_at": utc_now(),
                    }
                    write_entity_provenance(adapter, document, entity, updated)
                    changed.append((row, entity, original))
                revert_layer.LayerOn = False
            self._assert_geometry_unchanged(owned, snapshots)
            entity_updates = []
            for row, entity, _metadata in changed:
                entity_updates.append(
                    {
                        "handle": row["handle"],
                        "current_layer": REVERT_LAYER,
                        "metadata": read_entity_provenance(entity) or {},
                    }
                )
            self.store.update_task_entities_and_status(
                task_id,
                entity_updates=entity_updates,
                status="reverted",
            )
        except Exception:
            self._restore_entities(adapter, document, changed, snapshots)
            try:
                revert_layer.LayerOn = previous_layer_on
            except Exception:
                pass
            raise
        return {
            "success": True,
            "task_id": task_id,
            "object_count": len(changed),
            "objects": manifest,
            "geometry_unchanged": True,
            "hard_delete": False,
            "revert_layer": REVERT_LAYER,
        }

    def _load_owned_entities(
        self, document: Any, task: dict[str, Any]
    ) -> list[tuple[dict[str, Any], Any, dict[str, Any]]]:
        task_id = str(task["task_id"])
        rows = [
            row
            for row in self.store.get_ai_task_entities(task_id)
            if row["owned"] and row["operation"] == "create"
        ]
        if not rows:
            raise ValueError("Task has no owned created entities")
        result = []
        for row in rows:
            entity = document.HandleToObject(row["handle"])
            metadata = read_entity_provenance(entity)
            if not metadata or metadata.get("task_id") != task_id:
                raise PermissionError(
                    f"Entity {row['handle']} is not proven to belong to {task_id}"
                )
            if metadata.get("drawing_name") or metadata.get("drawing_full_name"):
                _assert_document_identity(
                    metadata,
                    document,
                    source=f"Entity {row['handle']} provenance",
                )
            result.append((row, entity, metadata))
        return result

    @staticmethod
    def _snapshot(
        owned: list[tuple[dict[str, Any], Any, dict[str, Any]]]
    ) -> dict[str, dict[str, Any]]:
        return {
            row["handle"]: {
                "layer": str(entity.Layer),
                "geometry": _geometry_signature(entity),
            }
            for row, entity, _metadata in owned
        }

    @staticmethod
    def _assert_geometry_unchanged(
        owned: list[tuple[dict[str, Any], Any, dict[str, Any]]],
        snapshots: dict[str, dict[str, Any]],
    ) -> None:
        for row, entity, _metadata in owned:
            if _geometry_signature(entity) != snapshots[row["handle"]]["geometry"]:
                raise RuntimeError(
                    f"Task operation changed geometry for handle {row['handle']}"
                )

    @staticmethod
    def _restore_entities(
        adapter: Any,
        document: Any,
        changed: list[tuple[dict[str, Any], Any, dict[str, Any]]],
        snapshots: dict[str, dict[str, Any]],
    ) -> None:
        for row, entity, metadata in reversed(changed):
            try:
                entity.Layer = snapshots[row["handle"]]["layer"]
                write_entity_provenance(adapter, document, entity, metadata)
            except Exception:
                pass
