from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

import cad_vision.analyzer as analyzer_module
from cad_vision.analyzer import analyze_source, vision_capabilities
from cad_vision.pdf import extract_vector_pdf


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


def _make_mixed_pdf(path: Path, page_modes: list[str]) -> None:
    fitz = pytest.importorskip("fitz")
    from PIL import Image, ImageDraw

    raster = Image.new("RGB", (600, 300), "white")
    draw = ImageDraw.Draw(raster)
    draw.text((40, 120), "DIA 20 +/- 0.1", fill="black")
    image_bytes = BytesIO()
    raster.save(image_bytes, format="PNG")

    document = fitz.open()
    for mode in page_modes:
        page = document.new_page(width=400, height=300)
        if mode in {"vector", "hybrid", "region", "logo"}:
            page.insert_text(fitz.Point(30, 30), "TITLE")
        if mode == "hybrid":
            page.insert_image(fitz.Rect(30, 70, 370, 260), stream=image_bytes.getvalue())
        elif mode == "region":
            page.insert_image(fitz.Rect(250, 80, 350, 160), stream=image_bytes.getvalue())
        elif mode == "raster":
            page.insert_image(fitz.Rect(20, 20, 380, 280), stream=image_bytes.getvalue())
        elif mode == "logo":
            page.insert_image(fitz.Rect(360, 10, 390, 30), stream=image_bytes.getvalue())
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


def test_vector_dimensions_use_distinct_line_boxes_inside_one_text_block(tmp_path: Path) -> None:
    fitz = pytest.importorskip("fitz")
    source = tmp_path / "two-lines.pdf"
    document = fitz.open()
    page = document.new_page(width=300, height=200)
    page.insert_textbox(fitz.Rect(30, 30, 180, 100), "DIA 20\nDIA 20", fontsize=12)
    document.save(source)
    document.close()

    extracted = extract_vector_pdf(source)
    dimensions = [item for item in extracted["dimensions"] if item["kind"] == "diameter"]

    assert len(dimensions) == 2
    assert dimensions[0]["bbox"] != dimensions[1]["bbox"]
    assert dimensions[0]["bbox"][3] <= dimensions[1]["bbox"][1]

    analyzed = analyze_source(str(source), use_cache=False, ocr_policy="off")
    analyzed_dimensions = [
        item for item in analyzed["analysis"]["dimensions"] if item["kind"] == "diameter"
    ]
    assert len(analyzed_dimensions) == 2


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
    assert result["analysis"]["ocr_targets_selected"] == [
        {"mode": "source", "reason": "raster_source"}
    ]


def test_hybrid_pdf_runs_page_ocr_even_when_vector_title_exists(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source = tmp_path / "hybrid.pdf"
    _make_mixed_pdf(source, ["hybrid"])
    captured: dict[str, Any] = {}

    def fake_ocr(*args: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "status": "ok",
            "provider": "paddleocr",
            "text_count": 1,
            "dimension_count": 1,
            "dimensions": [
                {
                    "kind": "diameter",
                    "value": 20.0,
                    "tolerance": 0.1,
                    "unit": None,
                    "page": 1,
                    "bbox": [50, 100, 180, 130],
                }
            ],
        }

    monkeypatch.setattr("cad_vision.analyzer.extract_ocr", fake_ocr)
    result = analyze_source(str(source), use_cache=False, use_ocr=True)

    assert captured["page_numbers"] == [1]
    assert captured["regions"] == {}
    assert result["analysis"]["pages"][0]["text_word_count"] == 1
    assert result["analysis"]["pages"][0]["raster_coverage_ratio"] > 0.15
    assert result["analysis"]["ocr_policy"] == "auto"
    assert result["analysis"]["ocr_coverage"]["complete"] is True
    assert result["analysis"]["dimensions"][0]["kind"] == "diameter"


def test_auto_ocr_targets_only_incomplete_pdf_pages(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "multipage.pdf"
    _make_mixed_pdf(source, ["vector", "raster"])
    captured: dict[str, Any] = {}

    def fake_ocr(*args: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"status": "ok", "provider": "paddleocr", "dimensions": []}

    monkeypatch.setattr("cad_vision.analyzer.extract_ocr", fake_ocr)
    result = analyze_source(str(source), use_cache=False, use_ocr=True)

    assert captured["page_numbers"] == [2]
    assert [target["page"] for target in result["analysis"]["ocr_targets_selected"]] == [2]


def test_auto_ocr_can_target_embedded_raster_region_without_rasterizing_page(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source = tmp_path / "region.pdf"
    _make_mixed_pdf(source, ["region"])
    captured: dict[str, Any] = {}

    def fake_ocr(*args: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"status": "ok", "provider": "paddleocr", "dimensions": []}

    monkeypatch.setattr("cad_vision.analyzer.extract_ocr", fake_ocr)
    result = analyze_source(str(source), use_cache=False, use_ocr=True)

    assert captured["page_numbers"] == []
    assert list(captured["regions"]) == [1]
    assert result["analysis"]["pages"][0]["ocr_recommendation"] == "regions"


def test_auto_ocr_ignores_small_logo_but_force_and_off_are_explicit(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source = tmp_path / "logo.pdf"
    _make_mixed_pdf(source, ["logo"])
    calls: list[dict[str, Any]] = []

    def fake_ocr(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"status": "ok", "provider": "paddleocr", "dimensions": []}

    monkeypatch.setattr("cad_vision.analyzer.extract_ocr", fake_ocr)
    automatic = analyze_source(str(source), use_cache=False, use_ocr=True)
    forced = analyze_source(str(source), use_cache=False, ocr_policy="force")
    disabled = analyze_source(str(source), use_cache=False, ocr_policy="off", use_ocr=True)

    assert automatic["analysis"]["ocr"]["status"] == "not_required"
    assert calls[0]["page_numbers"] == [1]
    assert forced["analysis"]["ocr_policy"] == "force"
    assert disabled["analysis"]["ocr"]["status"] == "disabled"
    assert len(calls) == 1


def test_off_policy_preserves_required_hybrid_pdf_ocr_gaps(tmp_path: Path) -> None:
    source = tmp_path / "hybrid-off.pdf"
    _make_mixed_pdf(source, ["hybrid"])

    result = analyze_source(str(source), use_cache=False, ocr_policy="off")

    analysis = result["analysis"]
    coverage = analysis["ocr_coverage"]
    assert analysis["ocr"]["status"] == "disabled"
    assert analysis["ocr_targets_selected"] == []
    assert analysis["ocr_targets_required"] == analysis["ocr_targets"]
    assert coverage["complete"] is False
    assert coverage["required"] is True
    assert coverage["execution_requested"] is False
    assert coverage["status"] == "disabled"
    assert coverage["gaps"] == analysis["ocr_targets"]


def test_off_policy_marks_raster_source_ocr_as_not_run(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "scan-off.png"
    source.write_bytes(b"raster fixture")
    monkeypatch.setattr(
        "cad_vision.analyzer.analyze_image_geometry",
        lambda *args, **kwargs: {"mode": "raster_geometry", "dimensions": []},
    )

    result = analyze_source(str(source), use_cache=False, ocr_policy="off")

    analysis = result["analysis"]
    required = [{"mode": "source", "reason": "raster_source"}]
    assert analysis["ocr_targets_required"] == required
    assert analysis["ocr_coverage"] == {
        "complete": False,
        "required": True,
        "status": "disabled",
        "execution_requested": False,
        "gaps": required,
    }


@pytest.mark.parametrize("overlap", [True, False], ids=["same-location", "different-location"])
def test_vector_and_ocr_dimension_deduplication_is_location_aware(
    tmp_path: Path, monkeypatch: Any, overlap: bool
) -> None:
    source = tmp_path / "evidence.pdf"
    source.write_bytes(b"fixture")
    vector_bbox = [10, 10, 80, 30]
    ocr_bbox = [20, 12, 75, 28] if overlap else [200, 200, 260, 220]
    monkeypatch.setattr(
        "cad_vision.analyzer.extract_vector_pdf",
        lambda *args, **kwargs: {
            "mode": "vector_pdf",
            "page_count_analyzed": 1,
            "text_word_count": 1,
            "pages": [],
            "ocr_targets": [],
            "dimensions": [
                {
                    "kind": "diameter",
                    "value": 20.0,
                    "tolerance": 0.1,
                    "unit": "mm",
                    "page": 1,
                    "bbox": vector_bbox,
                }
            ],
        },
    )
    monkeypatch.setattr(
        "cad_vision.analyzer.extract_ocr",
        lambda *args, **kwargs: {
            "status": "ok",
            "provider": "paddleocr",
            "dimensions": [
                {
                    "kind": "diameter",
                    "value": 20.0,
                    "tolerance": 0.1,
                    "unit": "mm",
                    "page": 1,
                    "bbox": ocr_bbox,
                }
            ],
        },
    )

    result = analyze_source(str(source), use_cache=False, ocr_policy="force")
    dimensions = result["analysis"]["dimensions"]
    assert len(dimensions) == (1 if overlap else 2)
    if overlap:
        assert dimensions[0]["evidence_sources"] == ["ocr", "vector_text"]
        assert len(dimensions[0]["provenance"]) == 2


def test_large_vector_block_does_not_absorb_a_different_location(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source = tmp_path / "large-block.pdf"
    source.write_bytes(b"fixture")
    monkeypatch.setattr(
        "cad_vision.analyzer.extract_vector_pdf",
        lambda *args, **kwargs: {
            "mode": "vector_pdf",
            "page_count_analyzed": 1,
            "text_word_count": 1,
            "pages": [],
            "ocr_targets": [],
            "dimensions": [
                {
                    "kind": "diameter",
                    "value": 20.0,
                    "unit": "mm",
                    "page": 1,
                    "bbox": [0, 0, 400, 300],
                }
            ],
        },
    )
    monkeypatch.setattr(
        "cad_vision.analyzer.extract_ocr",
        lambda *args, **kwargs: {
            "status": "ok",
            "provider": "paddleocr",
            "dimensions": [
                {
                    "kind": "diameter",
                    "value": 20.0,
                    "unit": "mm",
                    "page": 1,
                    "bbox": [20, 20, 80, 40],
                }
            ],
        },
    )

    result = analyze_source(str(source), use_cache=False, ocr_policy="force")

    dimensions = result["analysis"]["dimensions"]
    assert len(dimensions) == 2
    assert [item["evidence_sources"] for item in dimensions] == [["vector_text"], ["ocr"]]


def test_repeated_ocr_evidence_at_one_location_merges_into_one_record(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source = tmp_path / "repeated-ocr.pdf"
    source.write_bytes(b"fixture")
    monkeypatch.setattr(
        "cad_vision.analyzer.extract_vector_pdf",
        lambda *args, **kwargs: {
            "mode": "vector_pdf",
            "page_count_analyzed": 1,
            "text_word_count": 1,
            "pages": [],
            "ocr_targets": [],
            "dimensions": [
                {
                    "kind": "diameter",
                    "value": 20.0,
                    "tolerance": 0.1,
                    "unit": "mm",
                    "page": 1,
                    "bbox": [10, 10, 80, 30],
                }
            ],
        },
    )
    monkeypatch.setattr(
        "cad_vision.analyzer.extract_ocr",
        lambda *args, **kwargs: {
            "status": "ok",
            "provider": "paddleocr",
            "dimensions": [
                {
                    "kind": "diameter",
                    "value": 20.0,
                    "tolerance": 0.1,
                    "unit": "mm",
                    "page": 1,
                    "bbox": [12, 11, 78, 29],
                },
                {
                    "kind": "diameter",
                    "value": 20.0,
                    "tolerance": 0.1,
                    "unit": "mm",
                    "page": 1,
                    "bbox": [14, 12, 76, 28],
                },
            ],
        },
    )

    result = analyze_source(str(source), use_cache=False, ocr_policy="force")

    dimensions = result["analysis"]["dimensions"]
    assert len(dimensions) == 1
    assert dimensions[0]["evidence_sources"] == ["ocr", "vector_text"]
    assert len(dimensions[0]["provenance"]) == 3


def test_unit_resolution_and_ocr_policy_change_cache_keys(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "fixture.pdf"
    cache = tmp_path / "cache"
    _make_vector_pdf(source)
    monkeypatch.setenv("MULTICAD_VISION_CACHE", str(cache))
    monkeypatch.setattr(
        "cad_vision.analyzer.extract_ocr",
        lambda *args, **kwargs: {"status": "ok", "provider": "paddleocr", "dimensions": []},
    )

    unresolved = analyze_source(str(source), use_cache=True, ocr_policy="off")
    metric = analyze_source(str(source), use_cache=True, ocr_policy="off", source_unit="mm")
    metric_warm = analyze_source(str(source), use_cache=True, ocr_policy="off", source_unit="mm")
    forced = analyze_source(str(source), use_cache=True, ocr_policy="force", source_unit="mm")

    assert unresolved["analysis"]["unit_resolution"]["status"] == "unresolved"
    assert unresolved["analysis"]["dimensions"][0]["unit"] is None
    assert metric["analysis"]["dimensions"][0]["unit"] == "mm"
    assert metric_warm["cache_hit"] is True
    assert forced["analysis"]["ocr_policy"] == "force"
    assert len(list(cache.glob("*.json"))) == 3


def test_provider_and_ocr_profile_drift_change_cache_keys(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "runtime-drift.pdf"
    cache = tmp_path / "cache"
    _make_vector_pdf(source)
    monkeypatch.setenv("MULTICAD_VISION_CACHE", str(cache))
    versions = {
        "PyMuPDF": "1.0",
        "paddleocr": "3.3",
        "paddlepaddle": "3.2",
    }
    monkeypatch.setattr(
        analyzer_module,
        "_safe_distribution_version",
        lambda distribution: versions.get(distribution),
    )
    monkeypatch.setattr(
        "cad_vision.analyzer.extract_ocr",
        lambda *args, **kwargs: {"status": "ok", "provider": "paddleocr", "dimensions": []},
    )

    cold = analyze_source(str(source), use_cache=True, ocr_policy="force")
    warm = analyze_source(str(source), use_cache=True, ocr_policy="force")
    versions["paddleocr"] = "3.4"
    provider_drift = analyze_source(str(source), use_cache=True, ocr_policy="force")
    monkeypatch.setattr(
        analyzer_module,
        "OCR_RUNTIME_PROFILE",
        {
            "provider": "paddleocr",
            "ocr_version": "PP-OCRv6",
            "engine": "paddle_static",
            "device": "cpu",
        },
    )
    model_drift = analyze_source(str(source), use_cache=True, ocr_policy="force")

    assert cold["cache_hit"] is False
    assert warm["cache_hit"] is True
    assert provider_drift["cache_hit"] is False
    assert model_drift["cache_hit"] is False
    assert cold["runtime_fingerprint"]["ocr"]["ocr_version"] == "PP-OCRv5"
    assert model_drift["runtime_fingerprint"]["ocr"]["ocr_version"] == "PP-OCRv6"
    assert len(list(cache.glob("*.json"))) == 3


def test_conflicting_source_and_drawing_units_remain_unresolved(tmp_path: Path) -> None:
    source = tmp_path / "fixture.pdf"
    _make_vector_pdf(source)

    result = analyze_source(
        str(source),
        use_cache=False,
        ocr_policy="off",
        source_unit="mm",
        drawing_unit="inch",
    )

    resolution = result["analysis"]["unit_resolution"]
    assert resolution["status"] == "conflict"
    assert resolution["unit"] is None
    assert all(item["unit"] is None for item in result["analysis"]["dimensions"])
