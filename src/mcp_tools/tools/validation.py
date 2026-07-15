"""MCP tools for plan validation, guarded execution, and post verification."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from cad_memory.database import DEFAULT_DATABASE_PATH, SQLiteMemoryStore
from cad_memory.executor import PlanExecutor
from cad_memory.models import DrawingPlan
from cad_memory.provenance import (
    document_identity,
    generate_task_id,
    read_entity_provenance,
)
from cad_memory.receipts import (
    VALIDATION_RECEIPTS,
    canonical_plan_hash,
    read_document_unit,
)
from cad_memory.validator import PlanValidator
from cad_memory.verifier import PostExecutionVerifier
from mcp_tools.decorators import cad_tool, get_current_adapter


def _json_result(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _store() -> SQLiteMemoryStore:
    path = Path(os.environ.get("MULTICAD_CAD_MEMORY_DB", str(DEFAULT_DATABASE_PATH)))
    return SQLiteMemoryStore(path)


def _rollback_task_handles(adapter: Any, task_id: str, handles: list[str]) -> bool:
    """Delete only newly created handles whose XData proves task ownership."""
    document = adapter._get_document("cad_execute_plan_persistence_rollback")
    rollback_ok = True
    for handle in reversed(list(dict.fromkeys(handles))):
        try:
            entity = document.HandleToObject(handle)
            metadata = read_entity_provenance(entity)
            if not metadata or metadata.get("task_id") != task_id:
                rollback_ok = False
                continue
            entity.Delete()
        except Exception:
            rollback_ok = False
    return rollback_ok


def register_validation_tools(mcp: Any) -> None:
    """Register the opt-in guarded drawing workflow."""

    @cad_tool(mcp, "cad_plan_validate")
    def cad_plan_validate(plan_json: str) -> str:
        """Validate a plan and issue a short-lived receipt for exact execution."""
        plan = DrawingPlan.model_validate_json(plan_json)
        adapter = get_current_adapter()
        document = adapter._get_document("cad_plan_validate")
        identity = document_identity(document)
        drawing_unit = read_document_unit(document)
        layers = adapter.list_layers()
        report = PlanValidator().validate(
            plan,
            available_layers=layers,
            drawing_unit=drawing_unit["name"],
        )
        payload = report.to_dict()
        payload["plan_hash"] = canonical_plan_hash(plan)
        payload["drawing_identity"] = identity
        payload["drawing_unit"] = drawing_unit
        payload["validation_receipt"] = None
        if report.passed:
            receipt = VALIDATION_RECEIPTS.issue(
                plan,
                identity,
                drawing_unit["code"],
            )
            payload["validation_receipt"] = receipt.public_dict()
        return _json_result(payload)

    @cad_tool(mcp, "cad_execute_plan")
    def cad_execute_plan(plan_json: str, validation_id: str) -> str:
        """Execute only the exact plan bound to an unexpired validation receipt."""
        plan = DrawingPlan.model_validate_json(plan_json)
        adapter = get_current_adapter()
        document = adapter._get_document("cad_execute_plan_task")
        identity = document_identity(document)
        drawing_unit = read_document_unit(document)
        receipt = VALIDATION_RECEIPTS.consume(
            validation_id,
            plan,
            identity,
            drawing_unit["code"],
        )
        task_id = generate_task_id()
        store = _store()
        pending = store.record_execution(
            task_name=plan.task_name,
            planned_data=plan.model_dump(mode="json"),
            actual_data={"task_id": task_id, "status": "pending"},
            passed=False,
            errors=["pending"],
        )
        store.create_ai_task(
            task_id=task_id,
            task_name=plan.task_name,
            drawing_name=identity["drawing_name"],
            drawing_full_name=identity["drawing_full_name"],
            drawing_profile=plan.drawing_profile or "",
            status="executing",
            execution_result_id=pending["id"],
            plan_data=plan.model_dump(mode="json"),
        )
        result: dict[str, Any] | None = None
        try:
            result = PlanExecutor().execute(
                adapter,
                plan,
                task_id=task_id,
                execution_result_id=pending["id"],
            )
            errors = [row.get("error") for row in result.get("results", []) if row.get("error")]
            store.update_execution_result(
                pending["id"],
                actual_data={
                    "task_id": task_id,
                    "handles": result.get("handles", []),
                    "results": result.get("results", []),
                    "rolled_back": result.get("rolled_back", False),
                },
                passed=bool(result.get("success")),
                errors=errors,
            )
            if result.get("success"):
                store.add_ai_task_entities(task_id, result["entity_records"])
                store.update_ai_task(task_id, status="executed")
            else:
                store.update_ai_task(task_id, status="failed")
            result["task_id"] = task_id
            result["execution_result_id"] = pending["id"]
            result["validation_id"] = receipt.validation_id
            result["plan_hash"] = receipt.plan_hash
        except Exception as exc:
            rolled_back = False
            if result and result.get("handles"):
                rolled_back = _rollback_task_handles(adapter, task_id, result["handles"])
            store.update_execution_result(
                pending["id"],
                actual_data={
                    "task_id": task_id,
                    "status": "exception",
                    "rolled_back": rolled_back,
                },
                passed=False,
                errors=[str(exc)],
            )
            store.update_ai_task(task_id, status="failed")
            raise
        return _json_result(result)

    @cad_tool(mcp, "cad_verify_execution")
    def cad_verify_execution(plan_json: str, handles_json: str, task_id: str = "") -> str:
        """Re-read actual AutoCAD objects and compare them with plan targets."""
        plan = DrawingPlan.model_validate_json(plan_json)
        handles = json.loads(handles_json)
        if not isinstance(handles, list) or not all(isinstance(item, str) for item in handles):
            raise ValueError("handles_json must be a JSON array of entity handles")
        adapter = get_current_adapter()
        result = PostExecutionVerifier().verify(adapter, plan, handles)
        store = _store()
        resolved_task_id = task_id or store.find_task_for_handles(handles) or ""
        provenance_errors = []
        if resolved_task_id:
            task = store.get_ai_task(resolved_task_id)
            if canonical_plan_hash(task["plan_data"]) != canonical_plan_hash(plan):
                provenance_errors.append(
                    "Verification plan does not match the plan recorded for the task"
                )
            document = adapter._get_document("cad_verify_execution_provenance")
            for index, (planned, handle) in enumerate(zip(plan.entities, handles)):
                if planned.operation != "create":
                    continue
                metadata = read_entity_provenance(document.HandleToObject(handle))
                if not metadata or metadata.get("task_id") != resolved_task_id:
                    provenance_errors.append(f"entity[{index}] {handle}: task provenance mismatch")
        else:
            provenance_errors.append("Unable to resolve one AI task for the handles")
        if provenance_errors:
            result["passed"] = False
            result.setdefault("errors", []).extend(provenance_errors)
        result["task_id"] = resolved_task_id or None
        verification_record = store.record_execution(
            task_name=f"{plan.task_name}:verification",
            planned_data=plan.model_dump(mode="json"),
            actual_data=result,
            passed=bool(result.get("passed")),
            errors=result.get("errors", []),
        )
        result["verification_result_id"] = verification_record["id"]
        if resolved_task_id:
            store.update_ai_task(
                resolved_task_id,
                status="verified" if result.get("passed") else "failed",
                verification_data=result,
            )
        return _json_result(result)
