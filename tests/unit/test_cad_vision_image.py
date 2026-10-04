"""Exercise circle detection with known image geometry and adversarial line work."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from cad_vision.analyzer import analyze_source
from cad_vision.image import _diagonal_segments, _supported_circles, analyze_image_geometry


def test_continuous_ink_recovers_boundary_when_hough_has_no_lines(
    tmp_path: Path, monkeypatch: Any
) -> None:
    cv2 = pytest.importorskip("cv2")
    source = tmp_path / "continuous.png"
    drawing = Image.new("RGB", (320, 240), "white")
    pen = ImageDraw.Draw(drawing)
    pen.line((80, 25, 80, 215), fill="black", width=3)
    pen.line((20, 120, 280, 120), fill="black", width=1)
    drawing.save(source)
    monkeypatch.setattr(cv2, "HoughLinesP", lambda *args, **kwargs: None)

    result = analyze_image_geometry(source)

    assert [80.0, 25.0, 80.0, 215.0] in result["line_samples"]
    assert result["raw_line_candidate_count"] == 0
    assert result["binary_line_candidate_count"] == 2


def test_ink_geometry_preserves_white_gaps_and_close_parallel_lines(
    tmp_path: Path, monkeypatch: Any
) -> None:
    cv2 = pytest.importorskip("cv2")
    source = tmp_path / "distinct.png"
    drawing = Image.new("RGB", (300, 220), "white")
    pen = ImageDraw.Draw(drawing)
    for y in (40, 43):
        pen.line((30, y, 270, y), fill="black", width=1)
    pen.line((40, 100, 130, 100), fill="black", width=1)
    pen.line((150, 100, 260, 100), fill="black", width=1)
    drawing.save(source)
    monkeypatch.setattr(cv2, "HoughLinesP", lambda *args, **kwargs: None)

    result = analyze_image_geometry(source)

    assert result["binary_line_candidate_count"] == 4
    assert [30.0, 40.0, 270.0, 40.0] in result["line_samples"]
    assert [30.0, 43.0, 270.0, 43.0] in result["line_samples"]
    assert [40.0, 100.0, 260.0, 100.0] not in result["line_samples"]


def _has_circle(circles: list, x: float, y: float, radius: float) -> bool:
    """Compare detected geometry with the independently drawn circle."""
    return any(math.hypot(cx - x, cy - y) <= 4 and abs(cr - radius) <= 4 for cx, cy, cr in circles)


def test_real_circles_survive_crosshairs_and_concentric_suppression(tmp_path: Path) -> None:
    pytest.importorskip("cv2")
    source = tmp_path / "concentric.png"
    drawing = Image.new("RGB", (500, 360), "white")
    pen = ImageDraw.Draw(drawing)
    for x, y, radii in [(125, 170, (75, 45)), (360, 170, (60, 30))]:
        for radius in radii:
            pen.ellipse((x - radius, y - radius, x + radius, y + radius), outline="black", width=2)
        pen.line((x - 95, y, x + 95, y), fill="black", width=1)
        pen.line((x, y - 95, x, y + 95), fill="black", width=1)
    drawing.save(source)

    result = analyze_image_geometry(source)

    for x, y, radius in [(125, 170, 75), (125, 170, 45), (360, 170, 60), (360, 170, 30)]:
        assert _has_circle(result["circle_samples"], x, y, radius)
    assert result["concentric_circle_candidate_count"] >= 2
    assert result["circle_candidate_count"] == 4


def test_staggered_ink_rows_do_not_invent_a_continuous_axis_line(
    tmp_path: Path, monkeypatch: Any
) -> None:
    cv2 = pytest.importorskip("cv2")
    source = tmp_path / "staggered.png"
    drawing = Image.new("RGB", (300, 220), "white")
    pen = ImageDraw.Draw(drawing)
    pen.line((30, 100, 210, 100), fill="black", width=1)
    pen.line((90, 101, 270, 101), fill="black", width=1)
    drawing.save(source)
    monkeypatch.setattr(cv2, "HoughLinesP", lambda *args, **kwargs: None)

    result = analyze_image_geometry(source)

    assert [30.0, 100.5, 270.0, 100.5] not in result["line_samples"]
    assert all(abs(line[2] - line[0]) <= 180 for line in result["line_samples"])


def test_hatching_and_straight_boundaries_do_not_become_circles(tmp_path: Path) -> None:
    pytest.importorskip("cv2")
    source = tmp_path / "hatching.png"
    drawing = Image.new("RGB", (500, 360), "white")
    pen = ImageDraw.Draw(drawing)
    pen.rectangle((50, 50, 450, 310), outline="black", width=2)
    for x in range(50, 450, 12):
        pen.line((x, 50, max(50, x - 160), 310), fill="black", width=2)
    drawing.save(source)

    result = analyze_image_geometry(source)

    assert result["line_candidate_count"] > 0
    assert result["raw_circle_candidate_count"] > 0
    assert result["circle_candidate_count"] == 0
    assert result["rejected_circle_candidate_count"] == result["raw_circle_candidate_count"]


def test_circle_filter_runs_before_sample_truncation(tmp_path: Path, monkeypatch: Any) -> None:
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    source = tmp_path / "one-circle.png"
    drawing = Image.new("RGB", (400, 260), "white")
    pen = ImageDraw.Draw(drawing)
    pen.ellipse((210, 60, 350, 200), outline="black", width=2)
    drawing.save(source)
    monkeypatch.setattr(
        cv2,
        "HoughCircles",
        lambda *args, **kwargs: np.array([[[80, 130, 60], [280, 130, 70]]], dtype=float),
    )

    result = analyze_image_geometry(source, sample_limit=1)

    assert result["raw_circle_candidate_count"] == 2
    assert result["rejected_circle_candidate_count"] == 1
    assert result["circle_candidate_count"] == 1
    assert _has_circle(result["circle_samples"], 280, 130, 70)
    assert len(result["circle_support_samples"]) == 1
    assert result["circle_support_samples"][0]["accepted"] is True


@pytest.mark.parametrize("mode", ["partial", "cropped"])
def test_partial_or_cropped_circumference_does_not_claim_full_circle(mode: str) -> None:
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    drawing = Image.new("L", (260, 260), "white")
    pen = ImageDraw.Draw(drawing)
    x = 130 if mode == "partial" else 10
    pen.arc((x - 80, 50, x + 80, 210), start=0, end=270, fill="black", width=2)
    gray = np.asarray(drawing)
    edges = cv2.Canny(gray, 50, 150)

    circles, evidence, rejected, recovered = _supported_circles(
        gray, edges, np.array([[[x, 130, 80]]], dtype=float), 86, 80
    )

    assert circles == []
    assert rejected == 1
    assert recovered == 0
    assert evidence[0]["accepted"] is False
    assert min(evidence[0]["quadrant_coverage"]) < 0.5


def test_circle_evidence_is_bounded_and_summary_omits_samples(
    tmp_path: Path, monkeypatch: Any
) -> None:
    pytest.importorskip("cv2")
    source = tmp_path / "blank.png"
    Image.new("RGB", (240, 180), "white").save(source)
    monkeypatch.setenv("MULTICAD_VISION_CACHE", str(tmp_path / "cache"))

    detailed = analyze_source(str(source), use_cache=True, ocr_policy="off")
    summary = analyze_source(str(source), use_cache=True, ocr_policy="off", include_samples=False)

    assert detailed["pipeline_version"] == "1.9.1"
    assert detailed["cache_hit"] is False
    assert summary["cache_hit"] is True
    assert detailed["analysis"]["circle_support_samples"] == []
    assert detailed["analysis"]["circle_candidate_count"] == 0
    assert "circle_support_samples" not in summary["analysis"]
    assert "circle_samples" not in summary["analysis"]


def test_public_turning_fixture_retains_both_labelled_circles() -> None:
    pytest.importorskip("cv2")
    source = Path(__file__).parents[1] / "fixtures" / "real_drawings" / "bracket.png"

    result = analyze_image_geometry(source)

    assert _has_circle(result["circle_samples"], 1713, 684, 316)
    assert _has_circle(result["circle_samples"], 1713, 684, 268)
    assert result["rejected_circle_candidate_count"] > 0
    # These source images have partial labels; do not assert global precision.
    assert result["circle_candidate_count"] < result["raw_circle_candidate_count"]


def test_public_hole_tip_segments_are_observed_from_ink() -> None:
    pytest.importorskip("cv2")
    from cad_vision.benchmark import geometry_matches

    source = Path(__file__).parents[1] / "fixtures" / "real_drawings" / "hole.png"
    result = analyze_image_geometry(source)
    candidates = [
        {"kind": "line", "start": line[:2], "end": line[2:]}
        for line in result["diagonal_line_samples"]
    ]
    for start, end in [([122, 409], [177, 442]), ([177, 442], [233, 409])]:
        assert any(
            geometry_matches({"kind": "line", "start": start, "end": end}, item, 5)
            for item in candidates
        )


def test_diagonal_extension_does_not_bridge_white_gap(tmp_path: Path) -> None:
    pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    drawing = Image.new("L", (320, 240), "white")
    pen = ImageDraw.Draw(drawing)
    pen.line((30, 40, 120, 100), fill="black", width=2)
    pen.line((150, 120, 270, 200), fill="black", width=2)

    lines, count = _diagonal_segments(np.asarray(drawing), 80)

    assert count >= 2
    assert all(not (min(line[0], line[2]) < 100 and max(line[0], line[2]) > 180) for line in lines)


def test_diagonal_angle_sampling_keeps_shorter_tip_amid_hatching() -> None:
    pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    drawing = Image.new("L", (500, 400), "white")
    pen = ImageDraw.Draw(drawing)
    for x in range(20, 380, 8):
        pen.line((x, 20, x + 80, 180), fill="black", width=1)
    pen.line((80, 260, 160, 310, 240, 260), fill="black", width=2)

    lines, count = _diagonal_segments(np.asarray(drawing), 12)

    assert count > len(lines) == 12
    assert any(min(line[1], line[3]) > 250 and max(line[1], line[3]) > 300 for line in lines)
