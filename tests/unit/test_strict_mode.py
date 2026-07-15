"""Tests for fail-closed legacy write policy in the enhanced server."""

import pytest

from mcp_tools.strict_mode import assert_legacy_action_allowed


def test_strict_mode_blocks_legacy_geometry_writes(monkeypatch):
    monkeypatch.setenv("MULTICAD_STRICT_GUARDED_WRITES", "1")
    for tool, action in (
        ("draw_entities", "create"),
        ("manage_entities", "move"),
        ("manage_entities", "delete"),
        ("manage_files", "save"),
        ("manage_blocks", "insert"),
        ("manage_session", "undo"),
    ):
        with pytest.raises(PermissionError, match="strict mode"):
            assert_legacy_action_allowed(tool, action)


def test_strict_mode_keeps_read_only_legacy_actions(monkeypatch):
    monkeypatch.setenv("MULTICAD_STRICT_GUARDED_WRITES", "true")
    for tool, action in (
        ("manage_entities", "select"),
        ("manage_layers", "list"),
        ("manage_files", "list"),
        ("manage_blocks", "info"),
        ("manage_session", "screenshot"),
    ):
        assert_legacy_action_allowed(tool, action)


def test_strict_mode_allows_only_known_preview_layer_creation(monkeypatch):
    monkeypatch.setenv("MULTICAD_STRICT_GUARDED_WRITES", "1")
    assert_legacy_action_allowed(
        "manage_layers",
        "create",
        {"name": "AI_PREVIEW_CENTER"},
    )
    with pytest.raises(PermissionError):
        assert_legacy_action_allowed(
            "manage_layers",
            "create",
            {"name": "USER_FORMAL_LAYER"},
        )


def test_original_server_can_disable_strict_mode(monkeypatch):
    monkeypatch.setenv("MULTICAD_STRICT_GUARDED_WRITES", "0")
    assert_legacy_action_allowed("draw_entities", "create")
    assert_legacy_action_allowed("manage_entities", "delete")
