"""Exercise PDF coordinate candidates and their non-production safety boundaries."""

from __future__ import annotations

import copy
import math

import pytest

from cad_vision.analyzer import analyze_source
from cad_vision.pdf import extract_vector_pdf
from cad_vision.vector_geometry import extract_geometry


def test_pdf_coordinates_circle_rectangle_and_rotation(tmp_path):
    """Geometry remains in unrotated PDF points, with curve fits marked approximate."""
    fitz = pytest.importorskip("fitz")
    path = tmp_path / "geometry.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=400, height=300)
        page.draw_rect(fitz.Rect(10, 20, 80, 90))
        page.draw_circle((180, 100), 20)
        page.draw_oval(fitz.Rect(230, 70, 290, 110))
        page.set_rotation(90)
        doc.new_page()
        doc.save(path)
    result = extract_vector_pdf(path, max_pages=1)
    page = result["pages"][0]
    assert result["pages_truncated"] and result["page_count_total"] == 2
    assert page["size_points"] == [400, 300]
    lines = [item for item in page["geometry_samples"] if item["kind"] == "line"]
    circles = [item for item in page["geometry_samples"] if item["kind"] == "circle"]
    assert len(lines) == 4 and len(circles) == 1
    assert lines[0]["start"] == [10, 20] and lines[0]["end"] == [80, 20]
    assert circles[0]["center"] == pytest.approx([180, 100])
    assert circles[0]["radius"] == pytest.approx(20)
    assert circles[0]["needs_confirmation"] and not page["geometry_is_cad_plan"]
    assert page["unsupported_geometry_items"] == 4
    compact = analyze_source(str(path), ocr_policy="off", use_cache=False, include_samples=False)
    assert "geometry_samples" not in compact["analysis"]["pages"][0]


def test_bounds_closures_close_parallel_and_unsupported():
    """Close parallel strokes remain distinct; omitted paths are counted explicitly."""
    items = [("l", (0, 0), (10, 0)), ("l", (0, 0.01), (10, 0.01))]
    result = extract_geometry([{"type": "s", "items": items, "closePath": True}], limit=1)
    assert result["geometry_candidate_count"] == 2 and result["geometry_samples_truncated"]
    assert result["unsupported_geometry_items"] == 1
    full = extract_geometry([{"type": "s", "items": items}])
    assert full["geometry_samples"][0]["start"] != full["geometry_samples"][1]["start"]
    triangle = extract_geometry(
        [
            {
                "type": "s",
                "closePath": True,
                "items": [("l", (0, 0), (10, 0)), ("l", (10, 0), (5, 5))],
            }
        ]
    )
    assert triangle["geometry_candidate_count"] == 3
    assert triangle["geometry_samples"][-1]["end"] == [0, 0]
    assert extract_geometry([{"type": "f", "items": items}])["skipped_nonstroke_paths"] == 1


def test_quadrilateral_and_empty_paths():
    """Quad boundaries are traced around the perimeter rather than diagonally."""
    fitz = pytest.importorskip("fitz")
    result = extract_geometry(
        [
            {"type": "s", "items": [("qu", fitz.Quad((0, 0), (10, 0), (0, 5), (10, 5)))]},
            {"type": "s", "items": []},
        ]
    )
    assert result["geometry_candidate_count"] == 4
    assert result["geometry_samples"][1]["end"] == [10, 5]


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_limits_fail(limit):
    """Malformed limits cannot silently truncate all evidence."""
    with pytest.raises(ValueError):
        extract_geometry([], limit=limit)


def test_dense_circle_fit_and_nearby_noncircles():
    """Dense circles are candidates, while ellipse, broken and reversed paths fail."""
    points = [
        (50 + 20 * math.cos(i * math.tau / 120), 60 + 20 * math.sin(i * math.tau / 120))
        for i in range(120)
    ]
    items = [("l", a, b) for a, b in zip(points, points[1:] + points[:1])]

    def run(value):
        return extract_geometry([{"type": "s", "items": value}])["geometry_samples"]

    fitted = run(items)
    assert len(fitted) == 1 and fitted[0]["kind"] == "circle" and fitted[0]["needs_confirmation"]
    assert fitted[0]["source_segment_count"] == 120
    assert fitted[0]["center"] == pytest.approx([50, 60])
    broken = copy.deepcopy(items)
    broken[-1] = ("l", points[-1], (0, 0))
    assert all(item["kind"] == "line" for item in run(broken))
    ellipse = [("l", (a[0] * 2, a[1]), (b[0] * 2, b[1])) for _, a, b in items]
    assert all(item["kind"] == "line" for item in run(ellipse))
    assert all(item["kind"] == "line" for item in run(items[:-10]))


def test_distorted_bezier_is_not_a_circle():
    """Reject shapes whose bounding box is square but control points are wrong."""
    fitz = pytest.importorskip("fitz")
    with fitz.open() as doc:
        page = doc.new_page()
        page.draw_circle((100, 100), 20)
        drawing = page.get_drawings()[0]
    items = drawing["items"]
    first = list(items[0])
    first[2] = fitz.Point(first[2].x + 3, first[2].y)
    items[0] = tuple(first)
    result = extract_geometry([drawing])
    assert not result["geometry_samples"] and result["unsupported_geometry_items"] == 4


def test_nonfinite_path_coordinates_fail_closed():
    """Malformed PDF coordinates must not leak NaN candidates into evidence."""
    with pytest.raises(ValueError, match="non-finite"):
        extract_geometry([{"type": "s", "items": [("l", (float("nan"), 0), (1, 1))]}])
