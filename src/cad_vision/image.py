"""Deterministic image normalization and primitive geometry detection."""

from __future__ import annotations

from pathlib import Path
from typing import Any


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
            circles.append(
                [round(float(x), 2), round(float(y), 2), round(float(radius), 2)]
            )

    result: dict[str, Any] = {
        "mode": "raster_geometry",
        "image_size_px": [int(image.shape[1]), int(image.shape[0])],
        "estimated_skew_degrees": skew,
        "residual_skew_degrees": residual,
        "line_candidate_count": 0 if raw_lines is None else int(len(raw_lines)),
        "circle_candidate_count": (
            0 if raw_circles is None else int(len(raw_circles[0]))
        ),
    }
    if include_samples:
        result["line_samples"] = lines
        result["circle_samples"] = circles
    return result
