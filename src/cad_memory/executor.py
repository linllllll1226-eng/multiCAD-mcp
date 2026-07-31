"""Execute a validated CAD plan as one best-effort undoable unit."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from .models import DrawingPlan, EntityPlan
from .provenance import (
    build_entity_provenance,
    document_identity,
    write_entity_provenance,
)
from .validator import PlanValidator


def _coord(value: Any) -> tuple[float, float, float]:
    values = list(value)
    if len(values) == 2:
        values.append(0.0)
    return float(values[0]), float(values[1]), float(values[2])


def _set_if_supported(entity: Any, name: str, value: Any) -> None:
    """Set an optional CAD property while surfacing setter failures."""
    try:
        getattr(entity, name)
    except AttributeError:
        return
    setattr(entity, name, value)


@contextmanager
def undo_group(adapter: Any) -> Iterator[None]:
    """Use one AutoCAD undo mark when the COM document supports it."""
    document = adapter._get_document("cad_execute_plan")
    started = False
    try:
        document.StartUndoMark()
        started = True
    except Exception:
        started = False
    try:
        yield
    finally:
        if started:
            try:
                document.EndUndoMark()
            except Exception:
                pass


class PlanExecutor:
    """Execute only plans that pass the pre-execution validator."""

    def __init__(self, validator: PlanValidator | None = None) -> None:
        """Initialize with an optional reusable plan validator."""
        self.validator = validator or PlanValidator()

    def execute(
        self,
        adapter: Any,
        plan: DrawingPlan,
        *,
        task_id: str | None = None,
        execution_result_id: int | None = None,
    ) -> dict[str, Any]:
        """Validate and execute a plan using the supplied CAD adapter."""
        layers = adapter.list_layers()
        report = self.validator.validate(plan, available_layers=layers)
        if not report.passed:
            return {
                "success": False,
                "blocked": True,
                "validation": report.to_dict(),
                "handles": [],
                "results": [],
            }

        results: list[dict[str, Any]] = []
        entity_records: list[dict[str, Any]] = []
        created_handles: list[str] = []
        document = adapter._get_document("cad_execute_plan")
        identity = document_identity(document)
        rolled_back = False
        execution_error: str | None = None
        rollback_diagnostics = self._empty_rollback_diagnostics()
        with undo_group(adapter):
            for index, entity in enumerate(plan.entities):
                handle: str | None = None
                try:
                    handle = self._execute_entity(adapter, entity)
                    owned = entity.operation == "create"
                    metadata: dict[str, Any] = {}
                    # The CAD API has completed creation at this point. Own the
                    # handle before any lookup or post-create work can fail.
                    if owned:
                        created_handles.append(handle)
                    cad_object = document.HandleToObject(handle)
                    self._finalize_entity(entity, cad_object)
                    if task_id and owned:
                        if execution_result_id is None:
                            raise ValueError("execution_result_id is required for task provenance")
                        metadata = build_entity_provenance(
                            task_id=task_id,
                            execution_result_id=execution_result_id,
                            drawing_profile=plan.drawing_profile or "",
                            source_type=entity.dimension_source,
                            confidence=entity.confidence,
                            approximate_reference=(
                                entity.dimension_source == "approximate_reference"
                            ),
                            drawing_name=identity["drawing_name"],
                            drawing_full_name=identity["drawing_full_name"],
                        )
                        write_entity_provenance(adapter, document, cad_object, metadata)
                    results.append(
                        {
                            "index": index,
                            "success": True,
                            "handle": handle,
                            "owned": owned,
                        }
                    )
                    entity_records.append(
                        {
                            "handle": handle,
                            "object_type": str(
                                getattr(cad_object, "ObjectName", entity.entity_type)
                            ),
                            "operation": entity.operation,
                            "owned": owned,
                            "preview_layer": entity.layer if owned else "",
                            "current_layer": str(getattr(cad_object, "Layer", entity.layer)),
                            "formal_layer": "",
                            "source_type": entity.dimension_source,
                            "confidence": entity.confidence,
                            "approximate_reference": (
                                entity.dimension_source == "approximate_reference"
                            ),
                            "metadata": metadata,
                        }
                    )
                except Exception as exc:
                    execution_error = str(exc)
                    results.append({"index": index, "success": False, "error": execution_error})
                    rollback_diagnostics = self._rollback_created_with_diagnostics(
                        document, created_handles
                    )
                    rolled_back = bool(rollback_diagnostics["fully_rolled_back"])
                    break

        try:
            adapter.refresh_view()
        except Exception:
            pass
        success = len(results) == len(plan.entities) and all(
            result.get("success") for result in results
        )
        handles = (
            [result["handle"] for result in results if result.get("success")] if success else []
        )
        return {
            "success": success,
            "blocked": False,
            "validation": report.to_dict(),
            "handles": handles,
            "results": results,
            "task_id": task_id,
            "entity_records": entity_records if success else [],
            "rolled_back": rolled_back,
            "execution_error": execution_error,
            "rollback_diagnostics": rollback_diagnostics,
            "undo_group_requested": True,
        }

    @staticmethod
    def _empty_rollback_diagnostics() -> dict[str, Any]:
        """Return the stable shape used when no rollback was attempted."""
        return {
            "attempted": [],
            "details": [],
            "succeeded": [],
            "failed": [],
            "fully_rolled_back": False,
        }

    @staticmethod
    def _rollback_created_with_diagnostics(document: Any, handles: list[str]) -> dict[str, Any]:
        """Delete owned handles and retain lookup/delete diagnostics per handle."""
        unique_handles = list(dict.fromkeys(handles))
        details: list[dict[str, Any]] = []
        succeeded: list[str] = []
        failed: list[dict[str, Any]] = []
        for handle in reversed(unique_handles):
            try:
                cad_object = document.HandleToObject(handle)
            except Exception as exc:
                detail = {
                    "handle": handle,
                    "status": "failed",
                    "stage": "lookup",
                    "error": str(exc),
                }
                details.append(detail)
                failed.append(detail)
                continue
            try:
                cad_object.Delete()
            except Exception as exc:
                detail = {
                    "handle": handle,
                    "status": "failed",
                    "stage": "delete",
                    "error": str(exc),
                }
                details.append(detail)
                failed.append(detail)
                continue
            details.append({"handle": handle, "status": "deleted"})
            succeeded.append(handle)
        return {
            "attempted": unique_handles,
            "details": details,
            "succeeded": succeeded,
            "failed": failed,
            "fully_rolled_back": not failed,
        }

    @staticmethod
    def _rollback_created(document: Any, handles: list[str]) -> bool:
        """Delete only objects newly created by the failed execution attempt."""
        return bool(
            PlanExecutor._rollback_created_with_diagnostics(document, handles)["fully_rolled_back"]
        )

    def _execute_entity(self, adapter: Any, entity: EntityPlan) -> str:
        if entity.operation == "layout_only":
            return self._layout_dimension(adapter, entity)
        if entity.operation != "create":
            raise NotImplementedError(
                "Validated modify/delete plans are intentionally not auto-executed yet"
            )

        kind = entity.entity_type.lower()
        c = entity.coordinates
        d = entity.dimensions
        common = (entity.layer, "white", 25)

        if kind == "line":
            return adapter.draw_line(
                _coord(c["start"]),
                _coord(c["end"]),
                *common,
                _skip_refresh=True,
            )
        if kind == "text":
            return adapter.draw_text(
                _coord(c["position"]),
                entity.text_override,
                float(d["height"]),
                float(d.get("rotation", 0.0)),
                entity.layer,
                "white",
                _skip_refresh=True,
            )
        if kind == "rectangle":
            return adapter.draw_rectangle(
                _coord(c["corner1"]),
                _coord(c["corner2"]),
                *common,
                _skip_refresh=True,
            )
        if kind == "circle":
            return adapter.draw_circle(
                _coord(c["center"]),
                float(d["radius"]),
                *common,
                _skip_refresh=True,
            )
        if kind == "arc":
            return adapter.draw_arc(
                _coord(c["center"]),
                float(d["radius"]),
                float(d["start_angle"]),
                float(d["end_angle"]),
                *common,
                _skip_refresh=True,
            )
        if kind == "polyline":
            return adapter.draw_polyline(
                [_coord(point) for point in c["points"]],
                bool(d.get("closed", False)),
                *common,
                _skip_refresh=True,
            )
        if kind in {"aligned_dimension", "linear_dimension"}:
            return adapter.add_dimension(
                _coord(c["start"]),
                _coord(c["end"]),
                None,
                entity.layer,
                "white",
                float(d.get("offset", 10.0)),
                _skip_refresh=True,
            )
        if kind == "diametric_dimension":
            return self._native_dimension(adapter, entity, radial=False)
        if kind == "radial_dimension":
            return self._native_dimension(adapter, entity, radial=True)
        raise ValueError(f"Unsupported execution entity type: {kind}")

    @staticmethod
    def _finalize_entity(entity: EntityPlan, cad_object: Any) -> None:
        """Apply post-create properties after the handle is owned for rollback."""
        if entity.operation != "create":
            return
        kind = entity.entity_type.lower()
        if kind in {"diametric_dimension", "radial_dimension"}:
            cad_object.Layer = entity.layer
            _set_if_supported(cad_object, "Linetype", entity.linetype)
            _set_if_supported(cad_object, "TextOverride", "")
            _set_if_supported(cad_object, "TextFill", False)
            _set_if_supported(cad_object, "UseBackgroundColor", False)
            return
        if entity.linetype and entity.linetype.lower() != "bylayer":
            cad_object.Linetype = entity.linetype

    def _native_dimension(self, adapter: Any, entity: EntityPlan, *, radial: bool) -> str:
        document = adapter._get_document("cad_execute_plan_dimension")
        c = entity.coordinates
        leader_length = float(entity.dimensions.get("leader_length", 10.0))
        if radial:
            dimension = document.ModelSpace.AddDimRadial(
                adapter._to_variant_array(_coord(c["center"])),
                adapter._to_variant_array(_coord(c["chord_point"])),
                leader_length,
            )
        else:
            dimension = document.ModelSpace.AddDimDiametric(
                adapter._to_variant_array(_coord(c["chord_point"])),
                adapter._to_variant_array(_coord(c["far_chord_point"])),
                leader_length,
            )
        return str(dimension.Handle)

    @staticmethod
    def _layout_dimension(adapter: Any, entity: EntityPlan) -> str:
        if len(entity.target_handles) != 1:
            raise ValueError("layout_only requires exactly one target handle")
        position = entity.coordinates.get("text_position")
        if position is None:
            raise ValueError("layout_only requires text_position")
        document = adapter._get_document("cad_dimension_layout")
        dimension = document.HandleToObject(entity.target_handles[0])
        before = {
            name: getattr(dimension, name, None)
            for name in ("ExtLine1Point", "ExtLine2Point", "XLine1Point", "XLine2Point")
        }
        dimension.TextPosition = adapter._to_variant_array(_coord(position))
        after = {name: getattr(dimension, name, None) for name in before}
        if before != after:
            raise RuntimeError("Dimension layout changed measured geometry points")
        return str(dimension.Handle)
