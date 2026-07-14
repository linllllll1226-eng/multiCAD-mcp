"""MCP tools for AI task provenance, commit, and reversible revert."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from cad_memory.database import DEFAULT_DATABASE_PATH, SQLiteMemoryStore
from cad_memory.task_manager import TaskTrackingManager
from mcp_tools.decorators import cad_tool, get_current_adapter


def _store() -> SQLiteMemoryStore:
    path = Path(os.environ.get("MULTICAD_CAD_MEMORY_DB", str(DEFAULT_DATABASE_PATH)))
    return SQLiteMemoryStore(path)


def _result(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def register_task_tools(mcp: Any) -> None:
    """Register task-scoped read, commit, and reversible revert tools."""

    @cad_tool(mcp, "cad_list_ai_tasks")
    def cad_list_ai_tasks(status: str | None = None, limit: int = 100) -> str:
        """List AI tasks and count matching entities in the active drawing."""
        result = TaskTrackingManager(_store()).list_tasks(
            get_current_adapter(), status=status, limit=limit
        )
        return _result(result)

    @cad_tool(mcp, "cad_get_task_entities")
    def cad_get_task_entities(task_id: str) -> str:
        """Read entities whose XData proves ownership by the specified task."""
        result = TaskTrackingManager(_store()).get_task_entities(
            get_current_adapter(), task_id
        )
        return _result(result)

    @cad_tool(mcp, "cad_get_entity_provenance")
    def cad_get_entity_provenance(handle: str) -> str:
        """Read the assistant XData and actual CAD state for one handle."""
        result = TaskTrackingManager(_store()).get_entity_provenance(
            get_current_adapter(), handle
        )
        return _result(result)

    @cad_tool(mcp, "cad_commit_preview_task")
    def cad_commit_preview_task(
        task_id: str,
        layer_mapping_json: str = "{}",
        confirmed: bool = False,
    ) -> str:
        """Commit only one verified task; confirmed=false returns a manifest."""
        mapping = json.loads(layer_mapping_json)
        if not isinstance(mapping, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in mapping.items()
        ):
            raise ValueError(
                "layer_mapping_json must be a JSON string-to-string object"
            )
        result = TaskTrackingManager(_store()).commit_preview_task(
            get_current_adapter(),
            task_id,
            layer_mapping=mapping,
            confirmed=confirmed,
        )
        return _result(result)

    @cad_tool(mcp, "cad_revert_ai_task")
    def cad_revert_ai_task(
        task_id: str,
        confirmed: bool = False,
        allow_committed: bool = False,
    ) -> str:
        """Reversibly hide one owned task without global undo or hard deletion."""
        result = TaskTrackingManager(_store()).revert_task(
            get_current_adapter(),
            task_id,
            confirmed=confirmed,
            allow_committed=allow_committed,
        )
        return _result(result)
