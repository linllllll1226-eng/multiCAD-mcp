"""Fail-closed policy for legacy tools in the enhanced MCP entry point."""

from __future__ import annotations

import os
from typing import Any

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}

READ_ONLY_ACTIONS = {
    "manage_entities": {"select"},
    "manage_layers": {"list", "info", "is_on"},
    "manage_files": {"list", "switch"},
    "manage_blocks": {"list", "info", "get_attrs"},
    "manage_session": {
        "connect",
        "disconnect",
        "status",
        "list_supported",
        "check_running",
        "zoom_extents",
        "screenshot",
        "export_view",
    },
}

PREVIEW_LAYERS = {
    "AI_PREVIEW_OUTLINE",
    "AI_PREVIEW_CENTER",
    "AI_PREVIEW_HIDDEN",
    "AI_PREVIEW_HATCH",
    "AI_PREVIEW_DIM",
    "AI_UNCERTAIN",
}


def strict_guarded_writes_enabled() -> bool:
    """Return whether legacy CAD writes must be rejected."""
    value = os.environ.get("MULTICAD_STRICT_GUARDED_WRITES", "0")
    return value.strip().lower() in _TRUE_VALUES


def assert_legacy_action_allowed(
    tool_name: str,
    action: str,
    spec: dict[str, Any] | None = None,
) -> None:
    """Reject legacy writes while retaining bounded read-only compatibility."""
    if not strict_guarded_writes_enabled():
        return
    normalized_action = str(action or "").strip().lower()
    if tool_name == "draw_entities":
        raise PermissionError(
            "Legacy draw_entities is disabled in strict mode; use "
            "cad_plan_validate -> cad_execute_plan -> cad_verify_execution"
        )
    if tool_name == "manage_layers" and normalized_action == "create":
        layer_name = str((spec or {}).get("name") or "").upper()
        if layer_name in PREVIEW_LAYERS:
            return
    if normalized_action in READ_ONLY_ACTIONS.get(tool_name, set()):
        return
    raise PermissionError(
        f"Legacy write {tool_name}:{normalized_action or '<missing>'} is disabled "
        "in strict mode; use the guarded workflow"
    )
