"""Verify observed PDF raster geometry against independently drawn coordinates."""

from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from cad_vision.analyzer import analyze_source
from cad_vision.pdf import extract_vector_pdf
from cad_vision.pdf_raster import add_raster_geometry


def _hybrid(path: Path, rotation: int) -> None:
    fitz = pytest.importorskip("fitz")
    drawing = Image.new("RGB", (400, 300), "white")
    pen = ImageDraw.Draw(drawing)
    pen.rectangle((40, 40, 360, 260), outline="black", width=2)
    pen.ellipse((140, 90, 260, 210), outline="black", width=2)
    stream = io.BytesIO()
    drawing.save(stream, format="PNG")
    with fitz.open() as document:
        page = document.new_page(width=500, height=400)
        page.set_cropbox(fitz.Rect(50, 40, 450, 360))
        page.insert_image(fitz.Rect(70, 80, 270, 230), stream=stream.getvalue())
        page.draw_line((20, 20), (150, 20))
        page.insert_text((20, 35), "VECTOR TITLE")
        page.set_rotation(rotation)
        document.save(path)


@pytest.mark.parametrize("rotation", [0, 90, 270])
def test_hybrid_geometry_uses_canonical_crop_points_with_ocr_off(
    tmp_path: Path, rotation: int
) -> None:
    pytest.importorskip("cv2")
    source = tmp_path / "混合图纸.pdf"
    _hybrid(source, rotation)

    result = analyze_source(str(source), ocr_policy="off", use_cache=False)

    page = result["analysis"]["pages"][0]
    assert page["geometry_samples"][0]["start"] == [20.0, 20.0]
    assert page["geometry_samples"][0]["end"] == [150.0, 20.0]
    samples = page["raster_geometry_samples"]
    assert any(
        item["kind"] == "circle"
        and math.dist(item["center"], [170, 155]) < 2
        and abs(item["radius"] - 30) < 2
        for item in samples
    )
    # The independently drawn image rectangle's left boundary is x=90,
    # y=100..210 after its 0.5 image-to-PDF scale and region translation.
    assert any(
        item["kind"] == "line"
        and min(
            math.dist(item["start"], [90, 100]) + math.dist(item["end"], [90, 210]),
            math.dist(item["end"], [90, 100]) + math.dist(item["start"], [90, 210]),
        )
        < 4
        for item in samples
    )
    assert all(item["needs_confirmation"] for item in samples)
    assert not page["raster_geometry_is_cad_plan"]
    assert result["analysis"]["raster_geometry_complete"] is False


def test_pdf_raster_budget_omission_is_explicit(tmp_path: Path) -> None:
    pytest.importorskip("cv2")
    source = tmp_path / "budget.pdf"
    _hybrid(source, 90)
    analysis = extract_vector_pdf(source)

    add_raster_geometry(source, analysis, max_regions=0)

    assert analysis["raster_geometry_status"] == "partial"
    assert analysis["raster_geometry_regions_processed"] == 0
    assert analysis["raster_geometry_regions_omitted"] == 1
    assert analysis["pages"][0]["raster_geometry_samples"] == []
    assert analysis["pages"][0]["geometry_samples"]


def test_pdf_optional_dependency_failure_keeps_vectors(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "optional.pdf"
    _hybrid(source, 0)

    def unavailable() -> None:
        raise RuntimeError("optional image dependencies unavailable")

    monkeypatch.setattr("cad_vision.pdf_raster._dependencies", unavailable)
    result = analyze_source(str(source), ocr_policy="off", use_cache=False)

    assert result["analysis"]["raster_geometry_status"] == "unavailable"
    assert result["analysis"]["pages"][0]["geometry_samples"]


def test_cached_compact_pdf_result_hides_raster_geometry(tmp_path: Path, monkeypatch: Any) -> None:
    pytest.importorskip("cv2")
    source = tmp_path / "compact.pdf"
    _hybrid(source, 0)
    monkeypatch.setenv("MULTICAD_VISION_CACHE", str(tmp_path / "cache"))
    full = analyze_source(str(source), ocr_policy="off")
    compact = analyze_source(str(source), ocr_policy="off", include_samples=False)

    assert full["analysis"]["pages"][0]["raster_geometry_samples"]
    assert compact["cache_hit"]
    assert "raster_geometry_samples" not in compact["analysis"]["pages"][0]
