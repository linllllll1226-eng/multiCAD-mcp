"""MCP tools for the local SQLite CAD experience memory."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from cad_memory.database import DEFAULT_DATABASE_PATH, SQLiteMemoryStore


def _store() -> SQLiteMemoryStore:
    path = Path(os.environ.get("MULTICAD_CAD_MEMORY_DB", str(DEFAULT_DATABASE_PATH)))
    return SQLiteMemoryStore(path)


def _load_json(value: str, expected: type, field: str) -> Any:
    data = json.loads(value)
    if not isinstance(data, expected):
        raise ValueError(f"{field} must contain JSON {expected.__name__}")
    return data


def _result(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def register_memory_tools(mcp: Any) -> None:
    """Register local-only memory tools; no CAD or network connection is required."""

    @mcp.tool()
    def cad_memory_search(
        query: str,
        category: str | None = None,
        include_unconfirmed: bool = False,
        limit: int = 50,
    ) -> str:
        """
        Search corrections.

        Unconfirmed rows are excluded from enforceable results by default.
        """
        records = _store().search_corrections(
            query,
            category=category,
            include_unconfirmed=include_unconfirmed,
            limit=limit,
        )
        return _result({"count": len(records), "records": records})

    @mcp.tool()
    def cad_memory_add_correction(
        category: str,
        trigger: str,
        wrong_behavior: str,
        correct_behavior: str,
        context_json: str = "{}",
        confirmed_by_user: bool = False,
    ) -> str:
        """Add a correction. It is enforceable only when confirmed_by_user is true."""
        record = _store().add_correction(
            category=category,
            trigger=trigger,
            wrong_behavior=wrong_behavior,
            correct_behavior=correct_behavior,
            context=_load_json(context_json, dict, "context_json"),
            confirmed_by_user=confirmed_by_user,
        )
        return _result(record)

    @mcp.tool()
    def cad_memory_list(
        table: str = "corrections",
        limit: int = 100,
        include_unconfirmed: bool = False,
    ) -> str:
        """List records from corrections, drawing_profiles, or execution_results."""
        records = _store().list_records(
            table, limit=limit, include_unconfirmed=include_unconfirmed
        )
        return _result({"table": table, "count": len(records), "records": records})

    @mcp.tool()
    def cad_memory_delete(table: str, record_id: int, confirmed: bool = False) -> str:
        """Delete one local memory record; confirmed=true is mandatory."""
        deleted = _store().delete_record(table, record_id, confirmed=confirmed)
        return _result({"table": table, "id": record_id, "deleted": deleted})

    @mcp.tool()
    def cad_save_drawing_profile(
        name: str,
        unit: str,
        layer_rules_json: str,
        dimension_rules_json: str,
        notes: str = "",
    ) -> str:
        """Create or update a named local drawing profile."""
        record = _store().save_drawing_profile(
            name=name,
            unit=unit,
            layer_rules=_load_json(layer_rules_json, dict, "layer_rules_json"),
            dimension_rules=_load_json(
                dimension_rules_json, dict, "dimension_rules_json"
            ),
            notes=notes,
        )
        return _result(record)

    @mcp.tool()
    def cad_load_drawing_profile(name: str) -> str:
        """Load a named local drawing profile."""
        return _result(_store().load_drawing_profile(name))
