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
