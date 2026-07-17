from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from cad_vision.analyzer import analyze_source, vision_capabilities


def _make_vector_pdf(path: Path) -> None:
    fitz = pytest.importorskip("fitz")
    document = fitz.open()
    page = document.new_page(width=400, height=300)
    page.draw_rect(fitz.Rect(40, 80, 240, 180))
    page.draw_circle(fitz.Point(140, 130), 30)
    page.insert_text(fitz.Point(40, 40), "DIA 20")
    page.insert_text(fitz.Point(40, 60), "R10")
    document.save(path)
    document.close()


def test_capabilities_are_read_only_and_json_safe() -> None:
    payload = vision_capabilities()
    assert payload["pipeline_version"]
    assert ".pdf" in payload["supported_extensions"]
    json.dumps(payload)


def test_vector_pdf_analysis_and_cache(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "fixture.pdf"
    cache = tmp_path / "cache"
    _make_vector_pdf(source)
    monkeypatch.setenv("MULTICAD_VISION_CACHE", str(cache))

    cold = analyze_source(str(source), use_cache=True, include_samples=False)
    warm = analyze_source(str(source), use_cache=True, include_samples=False)

    assert cold["cache_hit"] is False
    assert warm["cache_hit"] is True
    assert cold["analysis"]["vector_path_groups"] >= 2
    parsed = {item["kind"] for item in cold["analysis"]["dimensions"]}
    assert {"diameter", "radius"}.issubset(parsed)


def test_sample_toggle_reuses_one_canonical_cache_entry(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "fixture.pdf"
    cache = tmp_path / "cache"
    _make_vector_pdf(source)
    monkeypatch.setenv("MULTICAD_VISION_CACHE", str(cache))

    compact = analyze_source(str(source), use_cache=True, include_samples=False)
    detailed = analyze_source(str(source), use_cache=True, include_samples=True)

    assert compact["cache_hit"] is False
    assert compact["samples_included"] is False
    assert "vector_samples" not in compact["analysis"]["pages"][0]
    assert detailed["cache_hit"] is True
    assert detailed["samples_included"] is True
    assert detailed["analysis"]["pages"][0]["vector_samples"]
    assert len(list(cache.glob("*.json"))) == 1


def test_rejects_unsupported_source(tmp_path: Path) -> None:
    source = tmp_path / "unsafe.exe"
    source.write_bytes(b"not an image")
    with pytest.raises(ValueError, match="Unsupported source extension"):
        analyze_source(str(source))


def test_raster_geometry_handles_current_opencv_shape(tmp_path: Path) -> None:
    pytest.importorskip("cv2")
    from PIL import Image, ImageDraw

    source = tmp_path / "rotated.png"
    image = Image.new("RGB", (800, 500), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 100, 650, 380), outline="black", width=5)
    image.rotate(5, fillcolor="white").save(source)

    result = analyze_source(str(source), use_cache=False, include_samples=False)
    assert result["analysis"]["line_candidate_count"] > 0
    assert abs(result["analysis"]["residual_skew_degrees"]) < 1.0


def test_raster_geometry_preserves_close_parallel_lines(tmp_path: Path) -> None:
    pytest.importorskip("cv2")
    from PIL import Image, ImageDraw

    source = tmp_path / "close-parallel-lines.png"
    image = Image.new("RGB", (500, 260), "white")
    draw = ImageDraw.Draw(image)
    draw.line((60, 100, 440, 100), fill="black", width=1)
    draw.line((60, 108, 440, 108), fill="black", width=1)
    image.save(source)

    result = analyze_source(str(source), use_cache=False, include_samples=True)
    analysis = result["analysis"]
    assert analysis["close_parallel_pair_count"] >= 1
    assert any(
        pair["axis"] == "horizontal" and 6.0 <= pair["gap_px"] <= 10.0
        for pair in analysis["close_parallel_pairs"]
    )


def test_raster_geometry_preserves_two_lines_only_three_pixels_apart(tmp_path: Path) -> None:
    pytest.importorskip("cv2")
    from PIL import Image, ImageDraw

    source = tmp_path / "very-close-lines.png"
    image = Image.new("RGB", (500, 260), "white")
    draw = ImageDraw.Draw(image)
    draw.line((60, 100, 440, 100), fill="black", width=1)
    draw.line((60, 103, 440, 103), fill="black", width=1)
    image.save(source)

    result = analyze_source(str(source), use_cache=False, include_samples=True)
    assert any(
        pair["axis"] == "horizontal"
        and 2.0 <= pair["gap_px"] <= 4.0
        and pair["detector"] == "binary_stroke"
        for pair in result["analysis"]["close_parallel_pairs"]
    )


def test_raster_geometry_does_not_split_one_thick_line_into_a_pair(tmp_path: Path) -> None:
    pytest.importorskip("cv2")
    from PIL import Image, ImageDraw

    source = tmp_path / "one-thick-line.png"
    image = Image.new("RGB", (500, 260), "white")
    draw = ImageDraw.Draw(image)
    draw.line((60, 100, 440, 100), fill="black", width=4)
    image.save(source)

    result = analyze_source(str(source), use_cache=False, include_samples=True)
    assert not any(
        pair["axis"] == "horizontal" and pair["gap_px"] < 6.0
        for pair in result["analysis"]["close_parallel_pairs"]
    )


def test_raster_source_routes_to_optional_ocr(tmp_path: Path, monkeypatch: Any) -> None:
    pytest.importorskip("cv2")
    from PIL import Image

    source = tmp_path / "scan.png"
    Image.new("RGB", (200, 100), "white").save(source)
    monkeypatch.setattr(
        "cad_vision.analyzer.extract_ocr",
        lambda *args, **kwargs: {
            "status": "ok",
            "provider": "paddleocr",
            "text_count": 1,
            "dimension_count": 1,
            "dimensions": [{"kind": "diameter", "value": 15.0}],
        },
    )

    result = analyze_source(str(source), use_cache=False, use_ocr=True)
    assert result["analysis"]["ocr"]["status"] == "ok"
    assert result["analysis"]["ocr"]["dimensions"][0]["value"] == 15.0
