"""Language normalization tests for the optional PaddleOCR provider."""

from __future__ import annotations

from pathlib import Path

from cad_vision.ocr import (
    _get_pipeline,
    _normalize_language,
    _predict_with_retry,
    clear_pipeline_cache,
)


def test_common_english_and_chinese_aliases_are_normalized():
    assert _normalize_language("eng") == "en"
    assert _normalize_language(" English ") == "en"
    assert _normalize_language("zh-CN") == "ch"
    assert _normalize_language("chinese_cht") == "chinese_cht"


def test_pipeline_cache_uses_normalized_language(monkeypatch):
    calls = []

    def fake_create(language, device):
        calls.append((language, device))
        return object()

    clear_pipeline_cache()
    monkeypatch.setattr("cad_vision.ocr._create_pipeline", fake_create)
    first = _get_pipeline("eng", "cpu")
    second = _get_pipeline("en", "cpu")
    assert first is second
    assert calls == [("en", "cpu")]
    clear_pipeline_cache()


def test_predict_rebuilds_corrupted_cached_pipeline_once(monkeypatch):
    class BrokenPipeline:
        def predict(self, *_args, **_kwargs):
            raise IndexError("invalid vector<bool> subscript")

    class FreshPipeline:
        def predict(self, *_args, **_kwargs):
            return ["ok"]

    clear_pipeline_cache()
    monkeypatch.setattr("cad_vision.ocr._create_pipeline", lambda *_args: FreshPipeline())
    results, rebuilt = _predict_with_retry(
        BrokenPipeline(),
        Path("drawing.png"),
        language="en",
        device="cpu",
        min_confidence=0.5,
        pipeline_factory=None,
    )
    assert results == ["ok"]
    assert rebuilt is True
    clear_pipeline_cache()


def test_predict_does_not_retry_injected_test_pipeline():
    class BrokenPipeline:
        def predict(self, *_args, **_kwargs):
            raise RuntimeError("synthetic failure")

    try:
        _predict_with_retry(
            BrokenPipeline(),
            Path("drawing.png"),
            language="en",
            device="cpu",
            min_confidence=0.5,
            pipeline_factory=lambda *_args: BrokenPipeline(),
        )
    except RuntimeError as exc:
        assert str(exc) == "synthetic failure"
    else:
        raise AssertionError("injected pipelines must not be retried")
