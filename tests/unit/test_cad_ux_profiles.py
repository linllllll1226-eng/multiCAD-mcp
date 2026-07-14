"""Tests for preset drawing profile validation and MCP argument mapping."""

import json
from pathlib import Path

from cad_ux.profiles import load_profiles, memory_tool_arguments

PROFILE_DIR = Path(__file__).resolve().parents[2] / "data" / "profiles"
EXPECTED = {
    "university_mechanical_drawing",
    "chemical_pfd",
    "chemical_pid",
    "general_2d",
    "image_reconstruction",
}


def test_all_preset_profiles_are_valid_and_complete():
    profiles = load_profiles(PROFILE_DIR)
    assert {profile["name"] for profile in profiles} == EXPECTED
    for profile in profiles:
        assert profile["unit"] == "mm"
        assert profile["text_height"] > 0
        assert profile["allowed_tolerance"] > 0
        assert profile["force_preview"] is True
        layers = {row["name"]: row for row in profile["layers"]}
        assert layers["AI_UNCERTAIN"]["linetype"] != "Continuous"
        assert "AI_PREVIEW_OUTLINE" in layers


def test_profiles_map_to_existing_memory_tool_parameters():
    for profile in load_profiles(PROFILE_DIR):
        arguments = memory_tool_arguments(profile)
        assert set(arguments) == {
            "name",
            "unit",
            "layer_rules_json",
            "dimension_rules_json",
            "notes",
        }
        layer_rules = json.loads(arguments["layer_rules_json"])
        dimension_rules = json.loads(arguments["dimension_rules_json"])
        assert layer_rules["force_preview"] is True
        assert layer_rules["uncertain_layer"] == "AI_UNCERTAIN"
        assert dimension_rules["text_override"] == ""
        assert dimension_rules["background_fill"] is False
        assert dimension_rules["default_save_directory"].startswith("D:\\AI\\")
