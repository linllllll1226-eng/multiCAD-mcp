from __future__ import annotations

from pathlib import Path
from typing import Any

import cad_vision.ocr as ocr_module
from cad_vision.ocr import extract_ocr


class _FakeResult:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.json = {"res": payload}


class _FakePipeline:
    def predict(self, source: str, **kwargs: Any) -> list[_FakeResult]:
        assert Path(source).name == "scan.png"
        assert kwargs["text_rec_score_thresh"] == 0.5
        return [
            _FakeResult(
                {
                    "page_index": 0,
                    "rec_texts": ["DIA 15", "R20", "NOISE"],
                    "rec_scores": [0.99, 0.96, 0.2],
                    "rec_boxes": [
                        [10, 20, 80, 40],
                        [[90, 20], [140, 20], [140, 40], [90, 40]],
                        [0, 0, 5, 5],
                    ],
                }
            )
        ]


def test_extracts_text_boxes_and_dimension_candidates(tmp_path: Path) -> None:
    source = tmp_path / "scan.png"
    source.write_bytes(b"fake image content")
    result = extract_ocr(
        source,
        pipeline_factory=lambda language, device: _FakePipeline(),
    )

    assert result["status"] == "ok"
    assert result["text_count"] == 2
    assert result["dimension_count"] == 2
    assert result["text_samples"][0]["bbox"] == [10.0, 20.0, 80.0, 40.0]
    assert {item["kind"] for item in result["dimensions"]} == {"diameter", "radius"}


def test_reports_provider_failure_without_crashing_geometry_pipeline(tmp_path: Path) -> None:
    source = tmp_path / "scan.png"
    source.write_bytes(b"fake image content")

    def broken_factory(language: str, device: str) -> Any:
        raise RuntimeError("model unavailable")

    result = extract_ocr(source, pipeline_factory=broken_factory)
    assert result["status"] == "error"
    assert result["error_type"] == "RuntimeError"
    assert "model unavailable" in result["error"]


def test_runtime_model_cache_avoids_the_windows_profile_by_default(
    tmp_path: Path, monkeypatch: Any
) -> None:
    model_cache = tmp_path / "ascii-model-cache"
    monkeypatch.delenv("PADDLE_PDX_CACHE_HOME", raising=False)
    monkeypatch.setattr(ocr_module, "DEFAULT_MODEL_CACHE", model_cache)

    configured = ocr_module._configure_runtime_paths()

    assert configured == model_cache
    assert model_cache.is_dir()
    assert ocr_module.os.environ["PADDLE_PDX_CACHE_HOME"] == str(model_cache)
