"""Task-scoped commit, provenance inspection, and reversible CAD reverts."""

from __future__ import annotations

import hashlib
import json
import math
import ntpath
from pathlib import Path
from typing import Any

from cad_vision.audit_renderer import render_task_audit
from cad_vision.manifest import canonical_manifest_hash

from .database import SQLiteMemoryStore
from .executor import undo_group
from .provenance import (
    document_identity,
    read_entity_provenance,
    utc_now,
    write_entity_provenance,
)
from .receipts import canonical_plan_hash
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


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _entity_snapshot_hash(records: list[dict[str, Any]]) -> str:
    snapshots = [
        {
            "handle": str(record.get("handle", "")),
            "actual": record.get("actual", {}),
        }
        for record in records
    ]
    snapshots.sort(key=lambda item: item["handle"])
    return _canonical_hash(snapshots)


def _source_audit_required(task: dict[str, Any]) -> bool:
    provenance = (task.get("plan_data") or {}).get("source_provenance") or {}
    return bool(
        provenance.get("kind") == "image_pdf_reconstruction" or provenance.get("manifest_required")
    )


def _geometry_signature(entity: Any) -> dict[str, Any]:
    state = read_entity_state(entity)
    # Layer changes may legitimately alter effective linetype and can cause
    # AutoCAD to recompute dimension text placement. Neither is model
    # geometry. Guard only the geometric/measured properties so commit/revert
    # still rejects coordinate or measurement changes without false positives.
    geometry_keys = {
        "object_type",
        "closed",
        "start",
        "end",
        "center",
        "radius",
        "diameter",
        "length",
        "measurement",
        "coordinates",
        "width",
        "height",
    }
    return {key: value for key, value in state.items() if key in geometry_keys}


def _geometry_values_equal(left: Any, right: Any, *, tolerance: float = 1e-9) -> bool:
    """Compare CAD geometry recursively while tolerating COM float noise."""
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=tolerance, abs_tol=tolerance)
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _geometry_values_equal(left[key], right[key], tolerance=tolerance) for key in left
        )
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(
            _geometry_values_equal(a, b, tolerance=tolerance) for a, b in zip(left, right)
        )
    return left == right


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


def _assert_document_identity(record: dict[str, Any], document: Any, *, source: str) -> None:
    if not _identity_matches(record, document):
        current = document_identity(document)
        recorded_label = record.get("drawing_full_name") or record.get("drawing_name")
        active_label = current["drawing_full_name"] or current["drawing_name"]
        raise PermissionError(
            f"{source} belongs to a different drawing: "
            f"recorded={recorded_label!r}, active={active_label!r}"
        )


def _task_summary(task: dict[str, Any]) -> dict[str, Any]:
    """Return stable task metadata without heavy plan or verification payloads."""
    fields = (
        "task_id",
        "task_name",
        "drawing_name",
        "drawing_full_name",
        "drawing_profile",
        "status",
        "execution_result_id",
        "created_at",
        "updated_at",
    )
    return {key: task.get(key) for key in fields if key in task}


class TaskTrackingManager:
    """Operate only on entities carrying a matching assistant task identifier."""

    def __init__(self, store: SQLiteMemoryStore) -> None:
        """Use the supplied local task store."""
        self.store = store

    def list_tasks(
        self,
        adapter: Any,
        *,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
        include_details: bool = False,
        include_active_counts: bool = False,
    ) -> dict[str, Any]:
        """List paginated task summaries and optional live entity counts."""
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        document = adapter._get_document("cad_list_ai_tasks")
        fetched = self.store.list_ai_tasks(
            status=status,
            limit=limit + 1,
            offset=offset,
            include_details=include_details,
        )
        has_more = len(fetched) > limit
        tasks = fetched[:limit]
        for task in tasks:
            drawing_match = _identity_matches(task, document)
            task["active_drawing_match"] = drawing_match
            task["recorded_entity_count"] = self.store.count_ai_task_entities(task["task_id"])
            task["active_entity_count"] = 0 if not drawing_match else None
            if drawing_match and include_active_counts:
                active = 0
                for row in self.store.get_ai_task_entities(task["task_id"]):
                    try:
                        entity = document.HandleToObject(row["handle"])
                        metadata = read_entity_provenance(entity)
                        if metadata and metadata.get("task_id") == task["task_id"]:
                            active += 1
                    except Exception:
                        pass
                task["active_entity_count"] = active
        return {
            "count": len(tasks),
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
            "next_offset": offset + limit if has_more else None,
            "include_details": include_details,
            "include_active_counts": include_active_counts,
            "tasks": tasks,
        }

    def get_task_entities(
        self,
        adapter: Any,
        task_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
        include_actual: bool = True,
        include_provenance: bool = False,
        include_task_details: bool = False,
    ) -> dict[str, Any]:
        """Read a bounded page of entities whose XData proves task ownership."""
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), 200))
        task = self.store.get_ai_task(task_id, include_entities=False)
        document = adapter._get_document("cad_get_task_entities")
        _assert_document_identity(task, document, source=f"Task {task_id}")
        entities = []
        missing = []
        rows = self.store.get_ai_task_entities(
            task_id,
            offset=offset,
            limit=limit + 1,
        )
        has_more = len(rows) > limit
        for row in rows[:limit]:
            try:
                entity = document.HandleToObject(row["handle"])
                metadata = read_entity_provenance(entity)
                if row["owned"] and (not metadata or metadata.get("task_id") != task_id):
                    raise PermissionError("Entity provenance does not match task")
                payload = dict(row)
                if include_actual:
                    payload["actual"] = read_entity_state(entity)
                if include_provenance:
                    payload["provenance"] = metadata
                entities.append(payload)
            except Exception as exc:
                missing.append({"handle": row["handle"], "error": str(exc)})
        return {
            "task": task if include_task_details else _task_summary(task),
            "total_recorded": self.store.count_ai_task_entities(task_id),
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
            "next_offset": offset + limit if has_more else None,
            "entities": entities,
            "missing": missing,
        }

    def get_entity_provenance(self, adapter: Any, handle: str) -> dict[str, Any]:
        """Return XData and actual state for one active drawing handle."""
        document = adapter._get_document("cad_get_entity_provenance")
        entity = document.HandleToObject(handle)
        return {
            "handle": handle,
            "provenance": read_entity_provenance(entity),
            "actual": read_entity_state(entity),
        }

    def render_task_audit(
        self,
        adapter: Any,
        task_id: str,
        *,
        width: int = 1600,
        height: int = 1000,
        expected_manifest: dict[str, Any] | None = None,
        source_path: str = "",
        source_page: int = 1,
    ) -> dict[str, Any]:
        """Render fresh task geometry without activating or capturing AutoCAD."""
        task = self.store.get_ai_task(task_id, include_entities=False)
        document = adapter._get_document("cad_render_task_audit")
        _assert_document_identity(task, document, source=f"Task {task_id}")
        plan_entities = list((task.get("plan_data") or {}).get("entities") or [])
        records = []
        missing = []
        for index, row in enumerate(self.store.get_ai_task_entities(task_id)):
            try:
                entity = document.HandleToObject(row["handle"])
                metadata = read_entity_provenance(entity)
                if row["owned"] and (not metadata or metadata.get("task_id") != task_id):
                    raise PermissionError("Entity provenance does not match task")
                records.append(
                    {
                        **row,
                        "actual": read_entity_state(entity),
                        "planned": plan_entities[index] if index < len(plan_entities) else {},
                    }
                )
            except Exception as exc:
                missing.append({"handle": row["handle"], "error": str(exc)})
        result = render_task_audit(
            task,
            records,
            width=width,
            height=height,
            expected_manifest=expected_manifest,
            source_path=source_path,
            source_page=source_page,
        )
        result["missing"] = missing
        result["source_entity_count"] = len(records)
        source_provenance = (task.get("plan_data") or {}).get("source_provenance") or {}
        manifest_provenance = (expected_manifest or {}).get("provenance") or {}
        expected_source_sha = str(source_provenance.get("source_sha256") or "")
        expected_manifest_sha = str(source_provenance.get("expected_manifest_sha256") or "")
        actual_manifest_sha = canonical_manifest_hash(expected_manifest or {})
        actual_source_sha = _file_sha256(source_path) if source_path else ""
        trusted_manifest = bool(
            expected_manifest
            and manifest_provenance.get("generated_by") == "cad_prepare_reconstruction"
            and expected_source_sha
            and expected_manifest_sha
            and actual_manifest_sha == expected_manifest_sha
            and manifest_provenance.get("source_sha256") == expected_source_sha
            and manifest_provenance.get("pipeline_version")
            == source_provenance.get("pipeline_version")
            and actual_source_sha == expected_source_sha
        )
        comparison_path = str(result.get("comparison_path") or "")
        artifact_present = bool(comparison_path and Path(comparison_path).is_file())
        manifest_passed = result.get("manifest_comparison", {}).get("passed") is True
        layout_passed = result.get("audit", {}).get("dimension_layout_passed") is True
        audit_passed = bool(
            trusted_manifest
            and manifest_passed
            and layout_passed
            and not missing
            and artifact_present
        )
        audit_data = {
            "version": "source-completeness-v1",
            "required": _source_audit_required(task),
            "passed": audit_passed,
            "trusted_manifest": trusted_manifest,
            "plan_hash": canonical_plan_hash(task["plan_data"]),
            "source_sha256": actual_source_sha,
            "source_path": str(Path(source_path).resolve()) if source_path else "",
            "source_page": int(source_page),
            "manifest_hash": actual_manifest_sha,
            "expected_manifest_hash": expected_manifest_sha,
            "manifest_provenance": {
                "generated_by": manifest_provenance.get("generated_by"),
                "source_sha256": manifest_provenance.get("source_sha256"),
                "pipeline_version": manifest_provenance.get("pipeline_version"),
            },
            "entity_snapshot_hash": _entity_snapshot_hash(records),
            "manifest_passed": manifest_passed,
            "dimension_layout_passed": layout_passed,
            "missing_entity_count": len(missing),
            "comparison_artifact": comparison_path,
            "comparison_artifact_present": artifact_present,
        }
        self.store.update_ai_task(
            task_id,
            status=task["status"],
            audit_data=audit_data,
        )
        result["audit_gate"] = audit_data
        return result

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
        if _source_audit_required(task):
            audit_data = task.get("audit_data") or {}
            source_provenance = (task.get("plan_data") or {}).get("source_provenance") or {}
            current_plan_hash = canonical_plan_hash(task["plan_data"])
            evidence_passed = bool(
                audit_data.get("passed")
                and audit_data.get("trusted_manifest")
                and audit_data.get("manifest_passed")
                and audit_data.get("dimension_layout_passed")
                and audit_data.get("missing_entity_count") == 0
                and audit_data.get("comparison_artifact_present")
            )
            if not evidence_passed:
                return {
                    "success": False,
                    "blocked": True,
                    "reason": "Source-completeness audit has not passed",
                    "audit_gate": audit_data,
                }
            if audit_data.get("plan_hash") != current_plan_hash:
                return {
                    "success": False,
                    "blocked": True,
                    "reason": "Source-completeness audit is stale for the current plan",
                    "audit_gate": audit_data,
                }
            if audit_data.get("source_sha256") != source_provenance.get(
                "source_sha256"
            ) or audit_data.get("expected_manifest_hash") != source_provenance.get(
                "expected_manifest_sha256"
            ):
                return {
                    "success": False,
                    "blocked": True,
                    "reason": (
                        "Source-completeness audit is not bound to the current source manifest"
                    ),
                    "audit_gate": audit_data,
                }
            audit_source_path = str(audit_data.get("source_path") or "")
            if (
                not audit_source_path
                or not Path(audit_source_path).is_file()
                or _file_sha256(audit_source_path) != audit_data.get("source_sha256")
            ):
                return {
                    "success": False,
                    "blocked": True,
                    "reason": "Source-completeness audit is stale for the source file",
                    "audit_gate": audit_data,
                }
            artifact = str(audit_data.get("comparison_artifact") or "")
            if not artifact or not Path(artifact).is_file():
                return {
                    "success": False,
                    "blocked": True,
                    "reason": "Source-completeness comparison artifact is missing",
                    "audit_gate": audit_data,
                }
        document = adapter._get_document("cad_commit_preview_task")
        _assert_document_identity(task, document, source=f"Task {task_id}")
        mapping = {**DEFAULT_FORMAL_LAYER_MAP, **(layer_mapping or {})}
        owned = self._load_owned_entities(document, task)
        if _source_audit_required(task):
            current_records = [
                {"handle": row["handle"], "actual": read_entity_state(entity)}
                for row, entity, _metadata in owned
            ]
            if (task.get("audit_data") or {}).get("entity_snapshot_hash") != _entity_snapshot_hash(
                current_records
            ):
                return {
                    "success": False,
                    "blocked": True,
                    "reason": "Source-completeness audit is stale for changed CAD entities",
                    "audit_gate": task.get("audit_data") or {},
                }
        manifest = []
        missing_layers: set[str] = set()
        for row, entity, metadata in owned:
            source_layer = str(entity.Layer)
            target_layer = mapping.get(source_layer)
            if not target_layer:
                raise ValueError(f"No formal layer mapping for {source_layer}")
            if row["approximate_reference"] and target_layer != "AI_UNCERTAIN":
                raise PermissionError("Approximate reference geometry cannot enter a formal layer")
            try:
                _get_layer(document, target_layer)
            except ValueError:
                missing_layers.add(target_layer)
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
                "missing_layers": sorted(missing_layers),
                "ready_to_commit": not missing_layers,
            }
        if missing_layers:
            return {
                "success": False,
                "blocked": True,
                "reason": "Required formal layers do not exist",
                "task_id": task_id,
                "object_count": len(manifest),
                "objects": manifest,
                "missing_layers": sorted(missing_layers),
            }
        snapshots = self._snapshot(owned)
        changed: list[tuple[dict[str, Any], Any, dict[str, Any]]] = []
        try:
            with undo_group(adapter):
                for row, entity, metadata in owned:
                    target_layer = mapping[str(entity.Layer)]
                    original = dict(metadata)
                    changed.append((row, entity, original))
                    entity.Layer = target_layer
                    updated = {
                        **metadata,
                        "status": "committed",
                        "formal_layer": target_layer,
                        "committed_at": utc_now(),
                    }
                    write_entity_provenance(adapter, document, entity, updated)
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
        except Exception as exc:
            restore_report = self._restore_entities(adapter, document, changed, snapshots)
            if not restore_report["fully_restored"]:
                raise RuntimeError(
                    f"{exc}; commit restoration failed: {restore_report['failed']}"
                ) from exc
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
                    changed.append((row, entity, original))
                    entity.Layer = REVERT_LAYER
                    updated = {
                        **metadata,
                        "status": "reverted",
                        "revert_from_layer": source_layer,
                        "reverted_at": utc_now(),
                    }
                    write_entity_provenance(adapter, document, entity, updated)
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
        except Exception as exc:
            restore_report = self._restore_entities(adapter, document, changed, snapshots)
            layer_restore_error: str | None = None
            try:
                revert_layer.LayerOn = previous_layer_on
            except Exception as restore_exc:
                layer_restore_error = str(restore_exc)
            if not restore_report["fully_restored"] or layer_restore_error:
                failures = list(restore_report["failed"])
                if layer_restore_error:
                    failures.append(
                        {
                            "scope": "revert_layer",
                            "stage": "restore",
                            "error": layer_restore_error,
                        }
                    )
                raise RuntimeError(f"{exc}; revert restoration failed: {failures}") from exc
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
        owned: list[tuple[dict[str, Any], Any, dict[str, Any]]],
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
            if not _geometry_values_equal(
                _geometry_signature(entity),
                snapshots[row["handle"]]["geometry"],
            ):
                raise RuntimeError(f"Task operation changed geometry for handle {row['handle']}")

    @staticmethod
    def _restore_entities(
        adapter: Any,
        document: Any,
        changed: list[tuple[dict[str, Any], Any, dict[str, Any]]],
        snapshots: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Restore changed entities and retain diagnostics for every handle."""
        details: list[dict[str, Any]] = []
        restored: list[str] = []
        failed: list[dict[str, Any]] = []
        for row, entity, metadata in reversed(changed):
            try:
                entity.Layer = snapshots[row["handle"]]["layer"]
                write_entity_provenance(adapter, document, entity, metadata)
            except Exception as exc:
                detail = {
                    "handle": row["handle"],
                    "status": "failed",
                    "stage": "restore",
                    "error": str(exc),
                }
                details.append(detail)
                failed.append(detail)
                continue
            details.append({"handle": row["handle"], "status": "restored"})
            restored.append(row["handle"])
        return {
            "attempted": [row["handle"] for row, _entity, _metadata in changed],
            "details": details,
            "restored": restored,
            "failed": failed,
            "fully_restored": not failed,
        }
