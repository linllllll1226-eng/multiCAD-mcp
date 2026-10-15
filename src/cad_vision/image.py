"""Deterministic image normalization and primitive geometry detection."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any


def _axis_line_clusters(raw_lines: Any) -> list[dict[str, Any]]:
    """Collapse stroke-edge duplicates while preserving nearby parallel lines."""
    if raw_lines is None:
        return []
    candidates: list[dict[str, Any]] = []
    for x1, y1, x2, y2 in raw_lines.reshape(-1, 4):
        dx, dy = float(x2 - x1), float(y2 - y1)
        angle = abs(float(math.degrees(math.atan2(dy, dx)))) % 180
        if min(angle, 180 - angle) <= 3.0:
            candidates.append(
                {
                    "axis": "horizontal",
                    "offset": (float(y1) + float(y2)) / 2.0,
                    "span": [float(min(x1, x2)), float(max(x1, x2))],
                }
            )
        elif abs(angle - 90.0) <= 3.0:
            candidates.append(
                {
                    "axis": "vertical",
                    "offset": (float(x1) + float(x2)) / 2.0,
                    "span": [float(min(y1, y2)), float(max(y1, y2))],
                }
            )

    clusters: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: (item["axis"], item["offset"])):
        matching = next(
            (
                cluster
                for cluster in reversed(clusters)
                if cluster["axis"] == candidate["axis"]
                and abs(cluster["offset"] - candidate["offset"]) <= 3.0
            ),
            None,
        )
        if matching is None:
            clusters.append({**candidate, "samples": 1})
            continue
        count = matching["samples"]
        matching["offset"] = (matching["offset"] * count + candidate["offset"]) / (count + 1)
        matching["span"][0] = min(matching["span"][0], candidate["span"][0])
        matching["span"][1] = max(matching["span"][1], candidate["span"][1])
        matching["samples"] = count + 1
    return clusters


def _close_parallel_pairs(raw_lines: Any, sample_limit: int) -> list[dict[str, Any]]:
    """Report distinct nearby lines so downstream planning cannot merge them silently."""
    clusters = _axis_line_clusters(raw_lines)
    pairs: list[dict[str, Any]] = []
    for index, first in enumerate(clusters):
        for second in clusters[index + 1 :]:
            if first["axis"] != second["axis"]:
                continue
            gap = abs(float(second["offset"]) - float(first["offset"]))
            if gap < 4.0 or gap > 20.0:
                continue
            overlap = min(first["span"][1], second["span"][1]) - max(
                first["span"][0], second["span"][0]
            )
            shorter = min(
                first["span"][1] - first["span"][0],
                second["span"][1] - second["span"][0],
            )
            if shorter <= 0 or overlap / shorter < 0.6:
                continue
            pairs.append(
                {
                    "axis": first["axis"],
                    "offsets": [round(first["offset"], 2), round(second["offset"], 2)],
                    "gap_px": round(gap, 2),
                    "overlap_span": [
                        round(max(first["span"][0], second["span"][0]), 2),
                        round(min(first["span"][1], second["span"][1]), 2),
                    ],
                }
            )
            if len(pairs) >= sample_limit:
                return pairs
    return pairs


def _runs(values: Any, minimum_length: int) -> list[list[float]]:
    """Return inclusive spans for contiguous true values."""
    indices = [int(index) for index in values.nonzero()[0]]
    if not indices:
        return []
    result: list[list[float]] = []
    start = previous = indices[0]
    for index in indices[1:]:
        if index == previous + 1:
            previous = index
            continue
        if previous - start + 1 >= minimum_length:
            result.append([float(start), float(previous)])
        start = previous = index
    if previous - start + 1 >= minimum_length:
        result.append([float(start), float(previous)])
    return result


def _binary_axis_clusters(gray: Any) -> list[dict[str, Any]]:
    """Find actual ink strokes instead of treating both edges as separate lines."""
    _cv2, np = _dependencies()
    ink = np.asarray(gray) < 160
    height, width = ink.shape[:2]
    minimum_length = max(24, min(height, width) // 25)
    candidates: list[dict[str, Any]] = []
    for offset in range(height):
        for span in _runs(ink[offset, :], minimum_length):
            candidates.append({"axis": "horizontal", "offset": float(offset), "span": span})
    for offset in range(width):
        for span in _runs(ink[:, offset], minimum_length):
            candidates.append({"axis": "vertical", "offset": float(offset), "span": span})

    clusters: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: (item["axis"], item["offset"])):
        matching = None
        for cluster in reversed(clusters):
            if cluster["axis"] != candidate["axis"]:
                continue
            if candidate["offset"] - cluster["last_offset"] > 1.0:
                break
            overlap = min(cluster["span"][1], candidate["span"][1]) - max(
                cluster["span"][0], candidate["span"][0]
            )
            shorter = min(
                cluster["span"][1] - cluster["span"][0],
                candidate["span"][1] - candidate["span"][0],
            )
            if shorter > 0 and overlap / shorter >= 0.6:
                matching = cluster
                break
        if matching is None:
            clusters.append(
                {
                    **candidate,
                    "first_offset": candidate["offset"],
                    "last_offset": candidate["offset"],
                    "samples": 1,
                }
            )
            continue
        count = matching["samples"]
        matching["offset"] = (matching["offset"] * count + candidate["offset"]) / (count + 1)
        matching["last_offset"] = candidate["offset"]
        matching["span"][0] = min(matching["span"][0], candidate["span"][0])
        matching["span"][1] = max(matching["span"][1], candidate["span"][1])
        matching["samples"] = count + 1
    return clusters


def _pairs_from_binary(gray: Any, sample_limit: int) -> list[dict[str, Any]]:
    """Detect close parallel ink strokes, including two lines only 2 px apart."""
    clusters = _binary_axis_clusters(gray)
    pairs: list[dict[str, Any]] = []
    for index, first in enumerate(clusters):
        for second in clusters[index + 1 :]:
            if first["axis"] != second["axis"]:
                continue
            gap = abs(float(second["offset"]) - float(first["offset"]))
            # Binary evidence only fills Hough's narrow-gap blind spot. Wider
            # pairs are more efficiently and reliably handled by Hough lines.
            if gap < 2.0 or gap >= 6.0:
                continue
            overlap = min(first["span"][1], second["span"][1]) - max(
                first["span"][0], second["span"][0]
            )
            shorter = min(
                first["span"][1] - first["span"][0],
                second["span"][1] - second["span"][0],
            )
            if shorter <= 0 or overlap / shorter < 0.6:
                continue
            pairs.append(
                {
                    "axis": first["axis"],
                    "offsets": [round(first["offset"], 2), round(second["offset"], 2)],
                    "gap_px": round(gap, 2),
                    "overlap_span": [
                        round(max(first["span"][0], second["span"][0]), 2),
                        round(min(first["span"][1], second["span"][1]), 2),
                    ],
                    "detector": "binary_stroke",
                }
            )
            if len(pairs) >= sample_limit:
                return pairs
    return pairs


def _merge_close_pairs(
    hough_pairs: list[dict[str, Any]],
    binary_pairs: list[dict[str, Any]],
    sample_limit: int,
) -> list[dict[str, Any]]:
    """Prefer ink-stroke evidence and deduplicate Hough reports."""
    merged = list(binary_pairs)
    for pair in hough_pairs:
        corroborated = any(
            pair["axis"] == item["axis"]
            and abs(pair["offsets"][0] - item["offsets"][0]) <= 2.0
            and abs(pair["offsets"][1] - item["offsets"][1]) <= 2.0
            for item in binary_pairs
        )
        # Very small Hough gaps often represent the two edges of one thick
        # stroke. Require binary-stroke corroboration below 6 px.
        if pair["gap_px"] < 6.0 and not corroborated:
            continue
        duplicate = any(
            pair["axis"] == item["axis"]
            and abs(pair["offsets"][0] - item["offsets"][0]) <= 2.0
            and abs(pair["offsets"][1] - item["offsets"][1]) <= 2.0
            for item in merged
        )
        if not duplicate:
            merged.append({**pair, "detector": "hough"})
        if len(merged) >= sample_limit:
            break
    return merged[:sample_limit]


def _dependencies() -> tuple[Any, Any]:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:  # pragma: no cover - dependency-specific
        raise RuntimeError(
            "Image geometry analysis requires the optional 'vision' dependencies"
        ) from exc
    return cv2, np


def _load_image(path: Path) -> Any:
    cv2, np = _dependencies()
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Unsupported or unreadable image: {path}")
    return image


def estimate_skew(gray: Any) -> float:
    """Estimate page skew in degrees from near-horizontal/vertical line work."""
    cv2, np = _dependencies()
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    minimum = max(30, min(gray.shape[:2]) // 8)
    lines = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 1800,
        threshold=max(35, minimum // 2),
        minLineLength=minimum,
        maxLineGap=12,
    )
    if lines is None:
        return 0.0
    angles: list[float] = []
    for x1, y1, x2, y2 in lines.reshape(-1, 4):
        angle = float(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
        residual = ((angle + 45.0) % 90.0) - 45.0
        if abs(residual) <= 20.0:
            angles.append(residual)
    return round(float(np.median(angles)), 4) if angles else 0.0


def _deskew(image: Any, angle: float) -> Any:
    cv2, _ = _dependencies()
    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2.0, height / 2.0), angle, 1.0)
    return cv2.warpAffine(
        image,
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )


def _circle_support(edges: Any, gradient_x: Any, gradient_y: Any, circles: Any) -> tuple[Any, Any]:
    """Measure radial edge support at 360 angles, including out-of-frame gaps."""
    _, np = _dependencies()
    candidates = np.asarray(circles, dtype=float).reshape(-1, 3)
    angles = np.linspace(0, 2 * math.pi, 360, endpoint=False)
    cosines, sines = np.cos(angles), np.sin(angles)
    offsets = np.arange(-8, 9)[None, :, None]
    radii = candidates[:, 2, None, None]
    tolerance = np.ceil(np.clip(radii * 0.015, 3, 8))
    px = np.rint(candidates[:, 0, None, None] + (radii + offsets) * cosines).astype(int)
    py = np.rint(candidates[:, 1, None, None] + (radii + offsets) * sines).astype(int)
    height, width = edges.shape[:2]
    valid = (px >= 0) & (px < width) & (py >= 0) & (py < height) & (abs(offsets) <= tolerance)
    px, py = np.clip(px, 0, width - 1), np.clip(py, 0, height - 1)
    gx, gy = gradient_x[py, px], gradient_y[py, px]
    alignment = abs(gx * cosines + gy * sines) / np.maximum(np.hypot(gx, gy), 1)
    supported = np.any(valid & (edges[py, px] > 0) & (alignment >= 0.85), axis=1)
    coverage = supported.mean(axis=1)
    sectors = supported.reshape(-1, 4, 90).mean(axis=2)
    return coverage, sectors


def _supported_circles(
    gray: Any, edges: Any, raw_circles: Any, max_radius: int, sample_limit: int
) -> tuple[list[list[float]], list[dict[str, Any]], int, int]:
    """Reject unsupported Hough circles and recover distinct concentric rings."""
    if raw_circles is None:
        return [], [], 0, 0
    cv2, np = _dependencies()
    gradient_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0)
    gradient_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1)
    accepted: list[list[float]] = []
    evidence: list[dict[str, Any]] = []
    rejected = 0
    for start in range(0, len(raw_circles[0]), 32):
        batch = raw_circles[0, start : start + 32]
        coverage, sectors = _circle_support(edges, gradient_x, gradient_y, batch)
        for circle, fraction, sector_fractions in zip(batch, coverage, sectors):
            supported = bool(fraction >= 0.65 and sector_fractions.min() >= 0.5)
            coordinates = [round(float(value), 2) for value in circle]
            evidence.append(
                {
                    "circle": coordinates,
                    "detector": "hough",
                    "angular_coverage": round(float(fraction), 4),
                    "quadrant_coverage": [round(float(value), 4) for value in sector_fractions],
                    "accepted": supported,
                }
            )
            if supported:
                accepted.append(coordinates)
            else:
                rejected += 1

    # Hough's minimum centre distance suppresses other radii at the same centre.
    # Search only supported centres; chunking bounds the temporary array sizes.
    recovered = 0
    for x, y, _ in accepted[:sample_limit]:
        supported_radii = np.zeros(max_radius + 1, dtype=bool)
        for start in range(5, max_radius + 1, 32):
            radii = np.arange(start, min(start + 32, max_radius + 1))
            batch = np.column_stack((np.full(len(radii), x), np.full(len(radii), y), radii))
            coverage, sectors = _circle_support(edges, gradient_x, gradient_y, batch)
            supported_radii[radii] = (coverage >= 0.65) & (sectors.min(axis=1) >= 0.5)
        for first, last in _runs(supported_radii, 1):
            radius = (first + last) / 2
            tolerance = max(3.0, min(8.0, radius * 0.015))
            duplicate = any(
                math.hypot(x - cx, y - cy) <= max(3.0, tolerance)
                and abs(radius - cr) <= 2 * tolerance + 2
                for cx, cy, cr in accepted
            )
            if duplicate:
                continue
            circle = [x, y, round(radius, 2)]
            coverage, sectors = _circle_support(edges, gradient_x, gradient_y, [circle])
            if coverage[0] < 0.65 or sectors[0].min() < 0.5:
                continue
            accepted.append(circle)
            recovered += 1
            evidence.append(
                {
                    "circle": circle,
                    "detector": "concentric_radial_edges",
                    "angular_coverage": round(float(coverage[0]), 4),
                    "quadrant_coverage": [round(float(value), 4) for value in sectors[0]],
                    "accepted": True,
                }
            )
    # Keep the support provenance for retained circles ahead of rejected samples.
    evidence.sort(key=lambda item: not item["accepted"])
    return accepted, evidence[:sample_limit], rejected, recovered


def _diagonal_segments(gray: Any, sample_limit: int) -> tuple[list[list[float]], int]:
    """Retain diverse oblique ink segments without bridging unsupported gaps."""
    cv2, np = _dependencies()
    detected = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD).detect(gray)[0]
    if detected is None:
        return [], 0
    height, width = gray.shape[:2]
    bins: dict[int, list[tuple[float, list[float]]]] = {}
    for x1, y1, x2, y2 in detected.reshape(-1, 4):
        length = math.hypot(x2 - x1, y2 - y1)
        angle = math.degrees(math.atan2(y2 - y1, x2 - x1)) % 180
        if length < 24 or min(angle, 180 - angle, abs(angle - 90)) <= 3:
            continue
        ux, uy = (x2 - x1) / length, (y2 - y1) / length

        def extend(x: float, y: float, direction: int) -> tuple[float, float]:
            last = (float(x), float(y))
            for distance in np.arange(0.5, min(12, length * 0.15) + 0.01, 0.5):
                px, py = x + direction * distance * ux, y + direction * distance * uy
                supported = False
                for offset in (-1.5, -1, -0.5, 0, 0.5, 1, 1.5):
                    ix, iy = round(px - offset * uy), round(py + offset * ux)
                    if 0 <= ix < width and 0 <= iy < height and gray[iy, ix] < 160:
                        supported = True
                        break
                if not supported:
                    break
                last = (float(px), float(py))
            return last

        start, end = extend(x1, y1, -1), extend(x2, y2, 1)
        line = [round(value, 3) for value in (*start, *end)]
        bins.setdefault(int(angle // 10), []).append((length, line))
    count = sum(len(group) for group in bins.values())
    for group in bins.values():
        group.sort(key=lambda item: item[0], reverse=True)
    # Dense hatching must not consume every sample and hide shorter boundaries
    # at other angles. This balances angle bins, without assigning line roles.
    samples: list[list[float]] = []
    rank = 0
    while len(samples) < min(sample_limit, count):
        for key in sorted(bins):
            if rank < len(bins[key]):
                samples.append(bins[key][rank][1])
                if len(samples) == sample_limit:
                    break
        rank += 1
    return samples, count


def analyze_image_geometry(
    path: Path,
    include_samples: bool = True,
    sample_limit: int = 80,
) -> dict[str, Any]:
    """Deskew a drawing image and report bounded line/circle candidates."""
    cv2, np = _dependencies()
    image = _load_image(path)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    skew = estimate_skew(gray)
    normalized = _deskew(image, skew)
    normalized_gray = cv2.cvtColor(normalized, cv2.COLOR_BGR2GRAY)
    residual = estimate_skew(normalized_gray)
    edges = cv2.Canny(normalized_gray, 50, 150, apertureSize=3)

    minimum = max(24, min(normalized_gray.shape[:2]) // 12)
    raw_lines = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 720,
        threshold=max(30, minimum // 2),
        minLineLength=minimum,
        maxLineGap=8,
    )
    # Continuous ink spans recover boundaries fragmented by the edge Hough
    # detector. Do not join separate spans across white gaps or merge nearby
    # physical strokes. Keep diagonal/remaining Hough candidates as fallback.
    ink_clusters = _binary_axis_clusters(normalized_gray)
    lines: list[list[float]] = []
    for cluster in sorted(
        ink_clusters, key=lambda item: item["span"][1] - item["span"][0], reverse=True
    ):
        offset = round(cluster["offset"], 3)
        axis_pixels = (
            normalized_gray[round(offset), :]
            if cluster["axis"] == "horizontal"
            else normalized_gray[:, round(offset)]
        )
        # Adjacent rows can have staggered endpoints. Sample the central ink
        # row/column instead of turning their union into an invented long line.
        for start, end in _runs(axis_pixels < 160, max(24, min(normalized_gray.shape) // 25)):
            if start > cluster["span"][1] or end < cluster["span"][0]:
                continue
            candidate = (
                [start, offset, end, offset]
                if cluster["axis"] == "horizontal"
                else [offset, start, offset, end]
            )
            if candidate not in lines:
                lines.append(candidate)
    binary_count = len(lines)
    if raw_lines is not None:
        for line in raw_lines.reshape(-1, 4):
            candidate = [float(value) for value in line]
            if candidate not in lines:
                lines.append(candidate)
    close_parallel_pairs = _merge_close_pairs(
        _close_parallel_pairs(raw_lines, sample_limit),
        _pairs_from_binary(normalized_gray, sample_limit),
        sample_limit,
    )
    diagonals, diagonal_count = _diagonal_segments(normalized_gray, sample_limit)

    # A 5 px median can erase the thin circle strokes in technical drawings.
    blurred = cv2.medianBlur(normalized_gray, 3)
    max_radius = max(10, min(normalized_gray.shape[:2]) // 3)
    raw_circles = cv2.HoughCircles(
        blurred,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=max(20, minimum),
        param1=100,
        param2=30,
        minRadius=5,
        maxRadius=max_radius,
    )
    circles, circle_support, rejected, recovered = _supported_circles(
        normalized_gray, edges, raw_circles, max_radius, sample_limit
    )

    result: dict[str, Any] = {
        "mode": "raster_geometry",
        "image_size_px": [int(image.shape[1]), int(image.shape[0])],
        "estimated_skew_degrees": skew,
        "residual_skew_degrees": residual,
        "line_candidate_count": len(lines),
        "raw_line_candidate_count": 0 if raw_lines is None else int(len(raw_lines)),
        "binary_line_candidate_count": binary_count,
        "line_samples_truncated": len(lines) > sample_limit,
        "diagonal_line_candidate_count": diagonal_count,
        "diagonal_line_samples_truncated": diagonal_count > len(diagonals),
        "circle_candidate_count": len(circles),
        "raw_circle_candidate_count": (0 if raw_circles is None else int(len(raw_circles[0]))),
        "rejected_circle_candidate_count": rejected,
        "concentric_circle_candidate_count": recovered,
        "close_parallel_pair_count": len(close_parallel_pairs),
    }
    if include_samples:
        result["line_samples"] = lines[:sample_limit]
        result["diagonal_line_samples"] = diagonals
        result["close_parallel_pairs"] = close_parallel_pairs
        result["circle_samples"] = circles[:sample_limit]
        result["circle_support_samples"] = circle_support
    return result
