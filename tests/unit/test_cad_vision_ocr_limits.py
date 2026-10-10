"""Resource profiles must not share native pipelines or persistent OCR results."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cad_vision import analyzer, ocr


@pytest.mark.parametrize(
    "value", [True, False, 0, -1, 63, 1537, 10**100, 960.0, float("nan"), float("inf")]
)
def test_detector_limit_rejects_invalid_explicit_values(value: Any) -> None:
    """Reject values that cannot describe a bounded detector resolution."""
    with pytest.raises(ValueError, match="integer from 64 to 1536"):
        ocr.configured_ocr_det_max_side(value)


@pytest.mark.parametrize(
    "value", ["", "True", "NaN", "Inf", "960.0", "1e3", "９６０", "-64", "99999999999999999999999"]
)
def test_detector_limit_rejects_invalid_environment(value: str, monkeypatch: Any) -> None:
    """Reject malformed operator configuration before loading native models."""
    monkeypatch.setenv("MULTICAD_OCR_DET_MAX_SIDE", value)
    with pytest.raises(ValueError, match="integer from 64 to 1536"):
        ocr.configured_ocr_det_max_side()


def test_detector_profile_defaults_and_explicit_value_wins(monkeypatch: Any) -> None:
    """Resolve the product default and honor explicit request precedence."""
    monkeypatch.delenv("MULTICAD_OCR_DET_MAX_SIDE", raising=False)
    assert ocr.configured_ocr_det_max_side() == 960
    monkeypatch.setenv("MULTICAD_OCR_DET_MAX_SIDE", " 1024 ")
    assert ocr.configured_ocr_det_max_side() == 1024
    assert ocr.configured_ocr_det_max_side(64) == 64
    assert ocr.configured_ocr_det_max_side(1536) == 1536


def test_native_factory_changes_detector_only(monkeypatch: Any) -> None:
    """Preserve the native backend, models, and orientation factory settings."""
    observed = []
    monkeypatch.setattr(ocr, "_configure_runtime_paths", lambda: Path("cached"))
    monkeypatch.setitem(
        sys.modules,
        "paddleocr",
        SimpleNamespace(PaddleOCR=lambda **kwargs: observed.append(kwargs)),
    )
    ocr._create_pipeline("eng", "cpu", 960)
    assert observed == [
        {
            "lang": "en",
            "ocr_version": "PP-OCRv5",
            "device": "cpu",
            "engine": "paddle_static",
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
            "text_det_limit_side_len": 960,
            "text_det_limit_type": "max",
        }
    ]


def test_configuration_change_rebuilds_and_evicts_previous_native_profile(monkeypatch: Any) -> None:
    """Avoid both wrong-profile reuse and retaining many model instances."""
    calls = []

    def create(language: str, device: str, size: int) -> object:
        calls.append((language, device, size))
        return object()

    monkeypatch.setattr(ocr, "_create_pipeline", create)
    monkeypatch.setenv("MULTICAD_OCR_DET_MAX_SIDE", "960")
    ocr.clear_pipeline_cache()
    try:
        first = ocr._get_pipeline("eng", "cpu")
        assert ocr._get_pipeline("en", "cpu") is first
        monkeypatch.setenv("MULTICAD_OCR_DET_MAX_SIDE", "1024")
        second = ocr._get_pipeline("en", "cpu")
        assert second is not first
        assert set(ocr._PIPELINES) == {("en", "cpu", 1024)}
        monkeypatch.setenv("MULTICAD_OCR_DET_MAX_SIDE", "960")
        assert ocr._get_pipeline("en", "cpu") is not first
        assert calls == [("en", "cpu", 960), ("en", "cpu", 1024), ("en", "cpu", 960)]
    finally:
        ocr.clear_pipeline_cache()


def test_profile_change_isolates_persistent_cache_and_request_settings(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Changing detector resolution must run OCR rather than reuse old results."""
    source = tmp_path / "source.png"
    source.write_bytes(b"mock geometry input")
    monkeypatch.setenv("MULTICAD_VISION_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("MULTICAD_OCR_DET_MAX_SIDE", "960")
    monkeypatch.setattr(
        analyzer, "analyze_image_geometry", lambda *_args, **_kwargs: {"dimensions": []}
    )
    calls = []

    def extract(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"status": "ok", "dimensions": []}

    monkeypatch.setattr(analyzer, "extract_ocr", extract)
    first = analyzer.analyze_source(str(source), ocr_policy="force")
    warm = analyzer.analyze_source(str(source), ocr_policy="force")
    monkeypatch.setenv("MULTICAD_OCR_DET_MAX_SIDE", "1024")
    changed = analyzer.analyze_source(str(source), ocr_policy="force")
    explicit = analyzer.analyze_source(str(source), ocr_policy="force", ocr_det_max_side=960)
    assert first["cache_hit"] is False and warm["cache_hit"] is True
    assert changed["cache_hit"] is False and explicit["cache_hit"] is True
    assert [call["det_max_side"] for call in calls] == [960, 1024]
    assert first["runtime_fingerprint"]["ocr"]["text_det_limit_side_len"] == 960
    assert changed["runtime_fingerprint"]["ocr"]["text_det_limit_side_len"] == 1024
    assert changed["runtime_fingerprint"]["ocr"]["text_det_limit_type"] == "max"


def test_legacy_profile_result_does_not_hit_new_detector_cache(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Previous factory-default cached results cannot satisfy the new profile."""
    source = tmp_path / "source.png"
    source.write_bytes(b"mock source")
    monkeypatch.setenv("MULTICAD_VISION_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("MULTICAD_OCR_DET_MAX_SIDE", raising=False)
    monkeypatch.setattr(
        analyzer, "analyze_image_geometry", lambda *_args, **_kwargs: {"dimensions": []}
    )
    calls = []
    monkeypatch.setattr(
        analyzer,
        "extract_ocr",
        lambda *_args, **kwargs: calls.append(kwargs) or {"status": "ok", "dimensions": []},
    )
    fingerprint = analyzer._runtime_fingerprint

    def legacy(**kwargs: Any) -> dict[str, Any]:
        value = fingerprint(**kwargs)
        value["ocr"].pop("text_det_limit_side_len")
        value["ocr"].pop("text_det_limit_type")
        return value

    monkeypatch.setattr(analyzer, "_runtime_fingerprint", legacy)
    analyzer.analyze_source(str(source), ocr_policy="force")
    monkeypatch.setattr(analyzer, "_runtime_fingerprint", fingerprint)
    current = analyzer.analyze_source(str(source), ocr_policy="force")
    assert current["cache_hit"] is False and len(calls) == 2


def test_disabled_ocr_does_not_parse_unrelated_invalid_configuration(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """OCR-disabled geometry analysis remains independent of OCR configuration."""
    source = tmp_path / "source.png"
    source.write_bytes(b"mock geometry input")
    monkeypatch.setenv("MULTICAD_OCR_DET_MAX_SIDE", "invalid")
    monkeypatch.setattr(
        analyzer, "analyze_image_geometry", lambda *_args, **_kwargs: {"dimensions": []}
    )
    result = analyzer.analyze_source(str(source), ocr_policy="off", use_cache=False)
    assert result["analysis"]["ocr"]["status"] == "disabled"
    assert "ocr" not in result["runtime_fingerprint"]
