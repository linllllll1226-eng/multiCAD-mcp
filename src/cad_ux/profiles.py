"""Validate file-based drawing profiles and map them to existing MCP tools."""

from __future__ import annotations

import json
from pathlib import Path, PureWindowsPath
from typing import Any

REQUIRED_FIELDS = {
    "name",
    "unit",
    "layers",
    "text_height",
    "dimension_rules",
    "allowed_tolerance",
    "force_preview",
    "default_save_directory",
}


def validate_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Return a validated profile without changing it."""
    missing = sorted(REQUIRED_FIELDS - profile.keys())
    if missing:
        raise ValueError(f"profile fields missing: {missing}")
    if not isinstance(profile["name"], str) or not profile["name"].strip():
        raise ValueError("profile name is required")
    if not isinstance(profile["unit"], str) or not profile["unit"].strip():
        raise ValueError("profile unit is required")
    if not isinstance(profile["layers"], list) or not profile["layers"]:
        raise ValueError("profile layers must be a non-empty list")

    names: set[str] = set()
    for layer in profile["layers"]:
        if not isinstance(layer, dict):
            raise ValueError("every layer rule must be an object")
        if not {"name", "color", "linetype"} <= layer.keys():
            raise ValueError("every layer needs name, color, and linetype")
        name = str(layer["name"]).strip()
        if not name or name in names:
            raise ValueError(f"invalid or duplicate layer name: {name!r}")
        names.add(name)
        color = layer["color"]
        if not isinstance(color, int) or not 1 <= color <= 255:
            raise ValueError(f"layer {name} color must be an ACI value from 1 to 255")
        if not isinstance(layer["linetype"], str) or not layer["linetype"].strip():
            raise ValueError(f"layer {name} linetype is required")

    if float(profile["text_height"]) <= 0:
        raise ValueError("text_height must be positive")
    if float(profile["allowed_tolerance"]) <= 0:
        raise ValueError("allowed_tolerance must be positive")
    if not isinstance(profile["dimension_rules"], dict):
        raise ValueError("dimension_rules must be an object")
    if not isinstance(profile["force_preview"], bool):
        raise ValueError("force_preview must be a boolean")
    if not PureWindowsPath(str(profile["default_save_directory"])).is_absolute():
        raise ValueError("default_save_directory must be an absolute Windows path")
    if "AI_UNCERTAIN" not in names:
        raise ValueError("every profile must define AI_UNCERTAIN")
    if profile["force_preview"] and not any(name.startswith("AI_PREVIEW_") for name in names):
        raise ValueError("force_preview profiles must define preview layers")
    return profile


def load_profile(path: str | Path) -> dict[str, Any]:
    """Load and validate one UTF-8 JSON profile."""
    profile_path = Path(path)
    value = json.loads(profile_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"profile must be a JSON object: {profile_path}")
    return validate_profile(value)


def load_profiles(directory: str | Path) -> list[dict[str, Any]]:
    """Load all profile JSON files and reject duplicate profile names."""
    profile_dir = Path(directory)
    profiles = [load_profile(path) for path in sorted(profile_dir.glob("*.json"))]
    names = [profile["name"] for profile in profiles]
    if len(names) != len(set(names)):
        raise ValueError("duplicate drawing profile names")
    return profiles


def memory_tool_arguments(profile: dict[str, Any]) -> dict[str, str]:
    """Map a profile to the unchanged cad_save_drawing_profile parameters."""
    validate_profile(profile)
    layer_rules = {
        "layers": profile["layers"],
        "force_preview": profile["force_preview"],
        "uncertain_layer": "AI_UNCERTAIN",
    }
    dimension_rules = dict(profile["dimension_rules"])
    dimension_rules.update(
        {
            "text_height": profile["text_height"],
            "allowed_tolerance": profile["allowed_tolerance"],
            "force_preview": profile["force_preview"],
            "default_save_directory": profile["default_save_directory"],
        }
    )
    return {
        "name": profile["name"],
        "unit": profile["unit"],
        "layer_rules_json": json.dumps(layer_rules, ensure_ascii=False),
        "dimension_rules_json": json.dumps(dimension_rules, ensure_ascii=False),
        "notes": str(profile.get("notes", "")),
    }
