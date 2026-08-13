from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import cad_vision.ocr as ocr_module
from cad_vision.ocr import extract_ocr
from cad_vision.pdf import extract_vector_pdf


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


def test_selected_pdf_region_maps_ocr_box_back_to_page_coordinates(tmp_path: Path) -> None:
    fitz = pytest.importorskip("fitz")
    source = tmp_path / "hybrid.pdf"
    document = fitz.open()
    document.new_page(width=200, height=150)
    document.save(source)
    document.close()

    class RegionPipeline:
        def predict(self, source_path: str, **kwargs: Any) -> list[_FakeResult]:
            assert Path(source_path).suffix == ".png"
            return [
                _FakeResult(
                    {
                        "rec_texts": ["DIA 20"],
                        "rec_scores": [0.99],
                        "rec_boxes": [[20, 40, 60, 80]],
                    }
                )
            ]

    result = extract_ocr(
        source,
        regions={1: [[10, 20, 110, 120]]},
        page_numbers=[],
        pdf_render_scale=2.0,
        pipeline_factory=lambda language, device: RegionPipeline(),
    )

    assert result["status"] == "ok"
    assert result["dimensions"][0]["page"] == 1
    assert result["dimensions"][0]["bbox"] == [20.0, 40.0, 40.0, 60.0]
    assert result["dimensions"][0]["provenance"][0]["provider"] == "paddleocr"


def test_rotated_cropped_pdf_region_maps_rendered_pixels_to_vector_coordinates(
    tmp_path: Path,
) -> None:
    fitz = pytest.importorskip("fitz")
    source = tmp_path / "rotated-cropped.pdf"
    document = fitz.open()
    page = document.new_page(width=400, height=300)
    page.set_cropbox(fitz.Rect(50, 40, 350, 260))
    image_rect = fitz.Rect(225, 100, 295, 180)
    white = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), False)
    white.clear_with(255)
    page.insert_image(image_rect, pixmap=white)
    page.insert_text((232, 145), "DIA 20", fontsize=20)
    page.set_rotation(90)
    document.save(source)
    document.close()

    vector = extract_vector_pdf(
        source,
        raster_page_threshold=1.1,
        raster_region_threshold=0.01,
    )
    assert vector["pages"][0]["size_points"] == [300.0, 220.0]
    assert vector["ocr_targets"][0]["mode"] == "regions"
    regions = vector["ocr_targets"][0]["regions"]
    assert regions[0] == pytest.approx([225.0, 100.0, 295.0, 180.0])
    vector_box = vector["dimensions"][0]["bbox"]

    class RenderedPixelPipeline:
        def predict(self, source_path: str, **kwargs: Any) -> list[_FakeResult]:
            rendered = fitz.Pixmap(source_path)
            samples = rendered.samples
            dark_pixels: list[tuple[int, int]] = []
            for y in range(rendered.height):
                for x in range(rendered.width):
                    index = (y * rendered.width + x) * rendered.n
                    if min(samples[index : index + min(rendered.n, 3)]) < 245:
                        dark_pixels.append((x, y))
            assert dark_pixels, "the canonical image region must contain rendered vector text"
            xs = [point[0] for point in dark_pixels]
            ys = [point[1] for point in dark_pixels]
            return [
                _FakeResult(
                    {
                        "rec_texts": ["DIA 20"],
                        "rec_scores": [0.99],
                        "rec_boxes": [[min(xs), min(ys), max(xs) + 1, max(ys) + 1]],
                    }
                )
            ]

    result = extract_ocr(
        source,
        regions={1: regions},
        page_numbers=[],
        pdf_render_scale=2.0,
        pipeline_factory=lambda language, device: RenderedPixelPipeline(),
    )

    assert result["status"] == "ok"
    ocr_box = result["dimensions"][0]["bbox"]
    # OCR boxes cover ink while PyMuPDF line boxes include font ascent/descent.
    # The mapped ink must still land inside the same canonical, unrotated line.
    assert ocr_box[0] >= vector_box[0] - 1.5
    assert ocr_box[1] >= vector_box[1] - 1.5
    assert ocr_box[2] <= vector_box[2] + 1.5
    assert ocr_box[3] <= vector_box[3] + 1.5
    assert (ocr_box[0] + ocr_box[2]) / 2 == pytest.approx(
        (vector_box[0] + vector_box[2]) / 2,
        abs=3.0,
    )


def test_pdf_render_failure_cleans_temporary_directory(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "broken.pdf"
    source.write_bytes(b"not opened because rendering is stubbed")
    cleaned: list[Path] = []

    class TrackingTemporaryDirectory:
        def __init__(self, prefix: str) -> None:
            self.name = str(tmp_path / f"{prefix}tracked")
            Path(self.name).mkdir()

        def cleanup(self) -> None:
            cleaned.append(Path(self.name))

    def fail_render(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("render failed")

    monkeypatch.setattr(ocr_module.tempfile, "TemporaryDirectory", TrackingTemporaryDirectory)
    monkeypatch.setattr(ocr_module, "_pdf_ocr_inputs", fail_render)

    result = extract_ocr(
        source,
        page_numbers=[1],
        pipeline_factory=lambda language, device: _FakePipeline(),
    )

    assert result["status"] == "error"
    assert result["error"] == "render failed"
    assert cleaned == [tmp_path / "multicad-ocr-tracked"]


def test_failed_retry_discards_rebuilt_pipeline_from_cache(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source = tmp_path / "scan.png"
    source.write_bytes(b"fake image content")

    class BrokenPipeline:
        def predict(self, source_path: str, **kwargs: Any) -> list[_FakeResult]:
            raise RuntimeError("native pipeline failed")

    first = BrokenPipeline()
    rebuilt = BrokenPipeline()
    ocr_module.clear_pipeline_cache()
    ocr_module._PIPELINES[("ch", "cpu")] = first
    monkeypatch.setattr(
        ocr_module,
        "ocr_capabilities",
        lambda: {"available": True},
    )
    monkeypatch.setattr(
        ocr_module,
        "_create_pipeline",
        lambda language, device: rebuilt,
    )

    try:
        result = extract_ocr(source)
        assert result["status"] == "error"
        assert result["error"] == "native pipeline failed"
        assert ("ch", "cpu") not in ocr_module._PIPELINES
    finally:
        ocr_module.clear_pipeline_cache()
