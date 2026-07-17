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
    lines: list[list[int]] = []
    if raw_lines is not None:
        for line in raw_lines.reshape(-1, 4)[:sample_limit]:
            lines.append([int(value) for value in line])
    close_parallel_pairs = _merge_close_pairs(
        _close_parallel_pairs(raw_lines, sample_limit),
        _pairs_from_binary(normalized_gray, sample_limit),
        sample_limit,
    )

    blurred = cv2.medianBlur(normalized_gray, 5)
    raw_circles = cv2.HoughCircles(
        blurred,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=max(20, minimum),
        param1=100,
        param2=30,
        minRadius=5,
        maxRadius=max(10, min(normalized_gray.shape[:2]) // 3),
    )
    circles: list[list[float]] = []
    if raw_circles is not None:
        for x, y, radius in raw_circles[0, :sample_limit]:
            circles.append([round(float(x), 2), round(float(y), 2), round(float(radius), 2)])

    result: dict[str, Any] = {
        "mode": "raster_geometry",
        "image_size_px": [int(image.shape[1]), int(image.shape[0])],
        "estimated_skew_degrees": skew,
        "residual_skew_degrees": residual,
        "line_candidate_count": 0 if raw_lines is None else int(len(raw_lines)),
        "circle_candidate_count": (0 if raw_circles is None else int(len(raw_circles[0]))),
        "close_parallel_pair_count": len(close_parallel_pairs),
    }
    if include_samples:
        result["line_samples"] = lines
        result["close_parallel_pairs"] = close_parallel_pairs
        result["circle_samples"] = circles
    return result
