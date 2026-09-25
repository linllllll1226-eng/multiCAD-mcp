"""Bounded vector candidates in PDF points; never a manufacturing-unit CAD plan."""

from __future__ import annotations

import math
from typing import Any


def _point(value: Any) -> list[float]:
    """Serialize a PyMuPDF point without losing close-boundary separation."""
    point = [float(value[0]), float(value[1])]
    if not all(math.isfinite(coordinate) for coordinate in point):
        raise ValueError("PDF geometry contains non-finite coordinates")
    return point


def _circle(items: list[Any]) -> dict[str, Any] | None:
    """Recognize only four joined standard quarter-circle cubic Beziers."""
    if len(items) != 4 or any(item[0] != "c" for item in items):
        return None
    starts = [_point(item[1]) for item in items]
    center = [sum(point[axis] for point in starts) / 4 for axis in (0, 1)]
    radius = math.dist(center, starts[0])
    if not math.isfinite(radius) or radius <= 0:
        return None
    tolerance = max(1e-4, radius * 5e-4)
    kappa = 4 * (math.sqrt(2) - 1) / 3
    for index, item in enumerate(items):
        start, first, second, end = [_point(point) for point in item[1:]]
        if math.dist(end, starts[(index + 1) % 4]) > tolerance:
            return None
        if abs(math.dist(center, start) - radius) > tolerance:
            return None
        if abs(math.dist(start, end) - math.sqrt(2) * radius) > tolerance:
            return None
        expected_first = [start[i] + kappa * (end[i] - center[i]) for i in (0, 1)]
        expected_second = [end[i] + kappa * (start[i] - center[i]) for i in (0, 1)]
        if max(math.dist(first, expected_first), math.dist(second, expected_second)) > tolerance:
            return None
    # Repeated semicircles or backtracking may also have four quarter segments.
    if any(math.dist(a, b) < radius for i, a in enumerate(starts) for b in starts[i + 1 :]):
        return None
    return {
        "kind": "circle",
        "center": center,
        "radius": radius,
        "approximation": "standard_cubic_circle_fit",
        "needs_confirmation": True,
    }


def _polyline_circle(items: list[Any]) -> dict[str, Any] | None:
    """Fit only dense, closed, monotonic circular polylines as review candidates."""
    if len(items) < 64 or any(item[0] != "l" for item in items):
        return None
    points = [_point(item[1]) for item in items]
    center = [(min(p[i] for p in points) + max(p[i] for p in points)) / 2 for i in (0, 1)]
    radii = [math.dist(center, point) for point in points]
    radius = sum(radii) / len(radii)
    if not math.isfinite(radius) or radius <= 0:
        return None
    tolerance = max(1e-4, radius * 1e-4)
    if any(abs(value - radius) > tolerance for value in radii):
        return None
    angles = [math.atan2(p[1] - center[1], p[0] - center[0]) for p in points]
    deltas = [
        (b - a + math.pi) % (2 * math.pi) - math.pi for a, b in zip(angles, angles[1:] + angles[:1])
    ]
    if any(abs(delta) < 1e-8 or abs(delta) > math.pi / 16 for delta in deltas):
        return None
    if not (all(delta > 0 for delta in deltas) or all(delta < 0 for delta in deltas)):
        return None
    if abs(abs(sum(deltas)) - 2 * math.pi) > 1e-6:
        return None
    if any(
        math.dist(_point(item[2]), points[(i + 1) % len(points)]) > tolerance
        for i, item in enumerate(items)
    ):
        return None
    return {
        "kind": "circle",
        "center": center,
        "radius": radius,
        "approximation": "dense_circular_polyline_fit",
        "needs_confirmation": True,
        "source_segment_count": len(items),
    }


def extract_geometry(drawings: list[dict[str, Any]], limit: int = 400) -> dict[str, Any]:
    """Retain real path coordinates, unsupported counts and explicit truncation."""
    if type(limit) is not int or limit < 1:
        raise ValueError("geometry limit must be a positive integer")
    candidates: list[dict[str, Any]] = []
    count = 0
    unsupported = 0
    skipped = 0

    def add(record: dict[str, Any], index: int, drawing: dict[str, Any]) -> None:
        """Count every candidate while bounding the serialized response."""
        nonlocal count
        count += 1
        if len(candidates) < limit:
            candidates.append(
                {
                    **record,
                    "path_index": index,
                    "source": "vector_path",
                    "coordinate_space": "unrotated_pdf_points",
                    "stroke_width": drawing.get("width"),
                    "dashes": drawing.get("dashes"),
                    "layer": drawing.get("layer"),
                }
            )

    for index, drawing in enumerate(drawings):
        if drawing.get("type") not in {"s", "fs"}:
            skipped += 1
            continue
        items = drawing.get("items", [])
        circle = _circle(items) or _polyline_circle(items)
        if circle is not None:
            add(circle, index, drawing)
            continue
        for item in items:
            if item[0] == "l":
                add(
                    {"kind": "line", "start": _point(item[1]), "end": _point(item[2])},
                    index,
                    drawing,
                )
            elif item[0] in {"re", "qu"}:
                shape = item[1]
                points = (
                    [shape.tl, shape.tr, shape.br, shape.bl]
                    if item[0] == "re"
                    else [shape.ul, shape.ur, shape.lr, shape.ll]
                )
                for start, end in zip(points, points[1:] + points[:1]):
                    add(
                        {"kind": "line", "start": _point(start), "end": _point(end)}, index, drawing
                    )
            else:
                unsupported += 1
        if drawing.get("closePath") and items and all(item[0] == "l" for item in items):
            connected = all(_point(a[2]) == _point(b[1]) for a, b in zip(items, items[1:]))
            if connected and _point(items[-1][2]) != _point(items[0][1]):
                add(
                    {"kind": "line", "start": _point(items[-1][2]), "end": _point(items[0][1])},
                    index,
                    drawing,
                )
            elif not connected:
                unsupported += 1
    return {
        "geometry_samples": candidates,
        "geometry_candidate_count": count,
        "geometry_samples_truncated": count > limit,
        "unsupported_geometry_items": unsupported,
        "skipped_nonstroke_paths": skipped,
        "geometry_is_cad_plan": False,
    }
