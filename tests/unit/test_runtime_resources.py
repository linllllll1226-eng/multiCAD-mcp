"""Verify bundled resources match checkout defaults and remain read-only inputs."""

import json
from pathlib import Path

import pytest

from cad_runtime import data_directory, resource_path
from cad_ux.profiles import load_profiles

ROOT = Path(__file__).resolve().parents[2]


def test_resource_copies_match_authoritative_checkout_files():
    assert resource_path("config.json").read_bytes() == (ROOT / "src/config.json").read_bytes()
    expected = sorted((ROOT / "data/profiles").glob("*.json"))
    actual = sorted(resource_path("profiles").glob("*.json"))
    assert len(actual) == len(expected) == 5
    for source, bundled in zip(expected, actual):
        assert source.name == bundled.name
        assert json.loads(source.read_text("utf-8")) == json.loads(bundled.read_text("utf-8"))
    assert len(load_profiles(resource_path("profiles"))) == 5


def test_resource_traversal_is_rejected():
    with pytest.raises(ValueError, match="inside"):
        resource_path("../config.json")


def test_runtime_data_override_is_separate_from_resources(tmp_path, monkeypatch):
    monkeypatch.setenv("MULTICAD_DATA_DIR", str(tmp_path))
    assert data_directory() == tmp_path
    assert resource_path("config.json").is_file()
