"""MCP tools for plan validation, guarded execution, and post verification."""

from __future__ import annotations

import json
from typing import Any

from cad_memory.database import SQLiteMemoryStore
from cad_memory.executor import PlanExecutor
from cad_memory.models import DrawingPlan
from cad_memory.validator import PlanValidator
from cad_memory.verifier import PostExecutionVerifier
from mcp_tools.decorators import cad_tool, get_current_adapter


def _json_result(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def register_validation_tools(mcp: Any) -> None:
    """Register the opt-in guarded drawing workflow."""

    @cad_tool(mcp, "cad_plan_validate")
    def cad_plan_validate(plan_json: str) -> str:
        """Validate a confirmed structured drawing plan against active CAD layers."""
        plan = DrawingPlan.model_validate_json(plan_json)
        layers = get_current_adapter().list_layers()
        report = PlanValidator().validate(plan, available_layers=layers)
        return _json_result(report.to_dict())

    @cad_tool(mcp, "cad_execute_plan")
    def cad_execute_plan(plan_json: str) -> str:
        """Execute a plan only after all pre-execution checks pass."""
        plan = DrawingPlan.model_validate_json(plan_json)
        result = PlanExecutor().execute(get_current_adapter(), plan)
        SQLiteMemoryStore().record_execution(
            task_name=plan.task_name,
            planned_data=plan.model_dump(mode="json"),
            actual_data={
                "handles": result.get("handles", []),
                "results": result.get("results", []),
            },
            passed=bool(result.get("success")),
            errors=[
                row.get("error")
                for row in result.get("results", [])
                if row.get("error")
            ],
        )
        return _json_result(result)

    @cad_tool(mcp, "cad_verify_execution")
    def cad_verify_execution(plan_json: str, handles_json: str) -> str:
        """Re-read actual AutoCAD objects and compare them with plan targets."""
        plan = DrawingPlan.model_validate_json(plan_json)
        handles = json.loads(handles_json)
        if not isinstance(handles, list) or not all(
            isinstance(item, str) for item in handles
        ):
            raise ValueError("handles_json must be a JSON array of entity handles")
        result = PostExecutionVerifier().verify(get_current_adapter(), plan, handles)
        SQLiteMemoryStore().record_execution(
            task_name=f"{plan.task_name}:verification",
            planned_data=plan.model_dump(mode="json"),
            actual_data=result,
            passed=bool(result.get("passed")),
            errors=result.get("errors", []),
        )
        return _json_result(result)
