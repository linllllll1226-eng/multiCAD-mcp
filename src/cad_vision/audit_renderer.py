"""Off-screen task rendering for background-safe AutoCAD visual audits.

The renderer consumes freshly read entity state and never asks AutoCAD to
activate, zoom, regenerate, or capture its window.  It therefore keeps working
when the application is covered, minimized, or on another virtual desktop.
"""

from __future__ import annotations

import html
import math
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .analyzer import _validated_source

DEFAULT_AUDIT_ROOT = Path(__file__).resolve().parents[2] / "data" / "audit_reports"
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


def _audit_font(size: int = 13) -> ImageFont.ImageFont:
    """Load a Unicode-capable font so engineering symbols render correctly."""
    candidates = [
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arial.ttf",
        Path(ImageFont.__file__).resolve().parent / "fonts" / "DejaVuSans.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(str(candidate), size=size)
        except (OSError, ValueError):
            continue
    return ImageFont.load_default()


@dataclass(frozen=True)
class Primitive:
    """Normalized 2D entity used by both SVG and PNG renderers."""

    kind: str
    points: tuple[tuple[float, float], ...]
    layer: str
    handle: str
    radius: float = 0.0
    start_angle: float = 0.0
    end_angle: float = 0.0
    closed: bool = False
    label: str = ""
    text_position: tuple[float, float] | None = None
    text_height: float = 0.0
    approximate: bool = False


def _point(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None


def _polyline_points(state: dict[str, Any]) -> tuple[tuple[float, float], ...]:
    coordinates = state.get("coordinates")
    if not isinstance(coordinates, list):
        return ()
    object_type = str(state.get("object_type", "")).lower()
    stride = 3 if "2dpolyline" in object_type else 2
    points = []
    for index in range(0, len(coordinates) - 1, stride):
        try:
            points.append((float(coordinates[index]), float(coordinates[index + 1])))
        except (TypeError, ValueError):
            return ()
    return tuple(points)


def _dimension_label(state: dict[str, Any], planned: dict[str, Any]) -> str:
    measurement = state.get("measurement")
    if not isinstance(measurement, (int, float)):
        measurement = planned.get("dimensions", {}).get("measurement")
    if not isinstance(measurement, (int, float)):
        return "DIM"
    value = f"{float(measurement):.6f}".rstrip("0").rstrip(".")
    kind = str(planned.get("entity_type") or state.get("object_type", "")).lower()
    if "diametric" in kind:
        return f"Ø{value}"
    if "radial" in kind:
        return f"R{value}"
    return value


def normalize_entities(records: Iterable[dict[str, Any]]) -> list[Primitive]:
    """Convert live CAD records into renderer primitives."""
    primitives: list[Primitive] = []
    for record in records:
        state = record.get("actual") or {}
        planned = record.get("planned") or {}
        kind = str(state.get("object_type") or planned.get("entity_type") or "").lower()
        layer = str(state.get("layer") or record.get("current_layer") or "0")
        handle = str(record.get("handle") or state.get("handle") or "")
        approximate = bool(record.get("approximate_reference"))
        if "polyline" in kind:
            points = _polyline_points(state)
            if len(points) >= 2:
                primitives.append(
                    Primitive(
                        "polyline",
                        points,
                        layer,
                        handle,
                        closed=bool(state.get("closed")),
                        approximate=approximate,
                    )
                )
        elif "line" in kind and "dimension" not in kind:
            start, end = _point(state.get("start")), _point(state.get("end"))
            if start and end:
                primitives.append(
                    Primitive("line", (start, end), layer, handle, approximate=approximate)
                )
        elif "circle" in kind:
            center = _point(state.get("center"))
            radius = state.get("radius")
            if center and isinstance(radius, (int, float)):
                primitives.append(
                    Primitive(
                        "circle",
                        (center,),
                        layer,
                        handle,
                        radius=float(radius),
                        approximate=approximate,
                    )
                )
        elif "arc" in kind:
            center = _point(state.get("center"))
            radius = state.get("radius")
            start = _point(state.get("start"))
            end = _point(state.get("end"))
            dimensions = planned.get("dimensions", {})
            if center and isinstance(radius, (int, float)):
                if start and end:
                    start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
                    end_angle = math.atan2(end[1] - center[1], end[0] - center[0])
                else:
                    start_angle = float(dimensions.get("start_angle", 0.0))
                    end_angle = float(dimensions.get("end_angle", 2 * math.pi))
                primitives.append(
                    Primitive(
                        "arc",
                        (center,),
                        layer,
                        handle,
                        radius=float(radius),
                        start_angle=start_angle,
                        end_angle=end_angle,
                        approximate=approximate,
                    )
                )
        elif "text" in kind:
            position = _point(
                state.get("position") or planned.get("coordinates", {}).get("position")
            )
            label = str(state.get("text") or planned.get("text_override") or "")
            text_height = state.get("text_height")
            if not isinstance(text_height, (int, float)):
                text_height = planned.get("dimensions", {}).get("height", 0.0)
            if position and label:
                # Plain engineering notes participate in the same presentation
                # gates as dimension text, without being counted as geometry.
                primitives.append(
                    Primitive(
                        "dimension",
                        (position,),
                        layer,
                        handle,
                        label=label,
                        text_position=position,
                        text_height=max(float(text_height or 0.0), 0.0),
                        approximate=approximate,
                    )
                )
        elif "dimension" in kind:
            coordinates = planned.get("coordinates", {})
            start = _point(coordinates.get("start") or coordinates.get("chord_point"))
            end = _point(coordinates.get("end") or coordinates.get("far_chord_point"))
            center = _point(coordinates.get("center"))
            text_position = _point(state.get("text_position"))
            points = tuple(point for point in (start, end, center, text_position) if point)
            if points:
                text_height = state.get("text_height")
                if not isinstance(text_height, (int, float)):
                    text_height = planned.get("dimensions", {}).get("text_height", 0.0)
                primitives.append(
                    Primitive(
                        "dimension",
                        points,
                        layer,
                        handle,
                        label=_dimension_label(state, planned),
                        text_position=text_position,
                        text_height=max(float(text_height or 0.0), 0.0),
                        approximate=approximate,
                    )
                )
    return primitives


def _sample_points(primitive: Primitive) -> list[tuple[float, float]]:
    if primitive.kind == "circle":
        x, y = primitive.points[0]
        r = primitive.radius
        return [(x - r, y - r), (x + r, y + r)]
    if primitive.kind == "arc":
        x, y = primitive.points[0]
        r = primitive.radius
        result = []
        start, end = primitive.start_angle, primitive.end_angle
        while end < start:
            end += 2 * math.pi
        for index in range(33):
            angle = start + (end - start) * index / 32
            result.append((x + r * math.cos(angle), y + r * math.sin(angle)))
        return result
    return list(primitive.points)


def _bounds(primitives: list[Primitive]) -> tuple[float, float, float, float]:
    points = [point for primitive in primitives for point in _sample_points(primitive)]
    if not points:
        raise ValueError("Task has no renderable 2D entities")
    xs, ys = [point[0] for point in points], [point[1] for point in points]
    min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
    if math.isclose(min_x, max_x):
        min_x, max_x = min_x - 0.5, max_x + 0.5
    if math.isclose(min_y, max_y):
        min_y, max_y = min_y - 0.5, max_y + 0.5
    return min_x, min_y, max_x, max_y


def _signature(primitive: Primitive) -> tuple[Any, ...]:
    def rounded(point: tuple[float, float]) -> tuple[float, float]:
        return round(point[0], 6), round(point[1], 6)

    points = tuple(rounded(point) for point in primitive.points)
    if primitive.kind == "line":
        points = tuple(sorted(points))
    return (
        primitive.kind,
        points,
        round(primitive.radius, 6),
        round(primitive.start_angle, 6),
        round(primitive.end_angle, 6),
        primitive.closed,
    )


def _rectangle_overlap(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
    gap: float = 0.0,
) -> bool:
    return not (
        first[2] + gap <= second[0]
        or second[2] + gap <= first[0]
        or first[3] + gap <= second[1]
        or second[3] + gap <= first[1]
    )


def _dimension_text_box(
    primitive: Primitive, fallback_height: float
) -> tuple[float, float, float, float] | None:
    position = primitive.text_position
    if position is None:
        return None
    height = primitive.text_height if primitive.text_height > 0 else fallback_height
    width = max(1.2 * height, 0.65 * height * max(len(primitive.label), 1))
    return (
        position[0] - width / 2,
        position[1] - height / 2,
        position[0] + width / 2,
        position[1] + height / 2,
    )


def _point_in_box(point: tuple[float, float], box: tuple[float, float, float, float]) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def _segments(primitive: Primitive) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    if primitive.kind == "line":
        return [(primitive.points[0], primitive.points[1])]
    if primitive.kind in {"polyline", "arc"}:
        points = (
            primitive.points if primitive.kind == "polyline" else tuple(_sample_points(primitive))
        )
        if primitive.kind == "polyline" and primitive.closed and points[0] != points[-1]:
            points = (*points, points[0])
        return list(zip(points, points[1:]))
    return []


def _orientation(
    first: tuple[float, float], second: tuple[float, float], third: tuple[float, float]
) -> float:
    return (second[1] - first[1]) * (third[0] - second[0]) - (second[0] - first[0]) * (
        third[1] - second[1]
    )


def _segments_intersect(
    first: tuple[float, float],
    second: tuple[float, float],
    third: tuple[float, float],
    fourth: tuple[float, float],
) -> bool:
    o1 = _orientation(first, second, third)
    o2 = _orientation(first, second, fourth)
    o3 = _orientation(third, fourth, first)
    o4 = _orientation(third, fourth, second)

    def on_segment(
        start: tuple[float, float],
        point: tuple[float, float],
        end: tuple[float, float],
    ) -> bool:
        return (
            min(start[0], end[0]) - 1e-9 <= point[0] <= max(start[0], end[0]) + 1e-9
            and min(start[1], end[1]) - 1e-9 <= point[1] <= max(start[1], end[1]) + 1e-9
        )

    if abs(o1) <= 1e-9 and on_segment(first, third, second):
        return True
    if abs(o2) <= 1e-9 and on_segment(first, fourth, second):
        return True
    if abs(o3) <= 1e-9 and on_segment(third, first, fourth):
        return True
    if abs(o4) <= 1e-9 and on_segment(third, second, fourth):
        return True
    return o1 * o2 < 0 and o3 * o4 < 0


def _segment_hits_box(
    start: tuple[float, float],
    end: tuple[float, float],
    box: tuple[float, float, float, float],
) -> bool:
    if _point_in_box(start, box) or _point_in_box(end, box):
        return True
    corners = ((box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3]))
    return any(
        _segments_intersect(start, end, corners[index], corners[(index + 1) % 4])
        for index in range(4)
    )


def _geometry_hits_box(primitive: Primitive, box: tuple[float, float, float, float]) -> bool:
    if primitive.kind == "circle":
        center = primitive.points[0]
        closest_x = min(max(center[0], box[0]), box[2])
        closest_y = min(max(center[1], box[1]), box[3])
        farthest_x = box[0] if abs(center[0] - box[0]) > abs(center[0] - box[2]) else box[2]
        farthest_y = box[1] if abs(center[1] - box[1]) > abs(center[1] - box[3]) else box[3]
        minimum = math.dist(center, (closest_x, closest_y))
        maximum = math.dist(center, (farthest_x, farthest_y))
        return minimum <= primitive.radius <= maximum
    return any(_segment_hits_box(start, end, box) for start, end in _segments(primitive))


def _distance_to_box(point: tuple[float, float], box: tuple[float, float, float, float]) -> float:
    dx = max(box[0] - point[0], 0.0, point[0] - box[2])
    dy = max(box[1] - point[1], 0.0, point[1] - box[3])
    return math.hypot(dx, dy)


def audit_primitives(
    primitives: list[Primitive], *, default_text_height: float = 3.5
) -> dict[str, Any]:
    """Return deterministic checks that complement model-based image review."""
    warnings: list[dict[str, Any]] = []
    signatures: dict[tuple[Any, ...], list[str]] = defaultdict(list)
    layer_counts = Counter()
    type_counts = Counter()
    for primitive in primitives:
        layer_counts[primitive.layer] += 1
        type_counts[primitive.kind] += 1
        signatures[_signature(primitive)].append(primitive.handle)
        if primitive.kind == "line" and math.dist(*primitive.points) <= 1e-9:
            warnings.append({"code": "ZERO_LENGTH", "handles": [primitive.handle]})
        if primitive.kind == "circle" and primitive.radius <= 0:
            warnings.append({"code": "INVALID_RADIUS", "handles": [primitive.handle]})
    for handles in signatures.values():
        if len(handles) > 1:
            warnings.append(
                {"code": "DUPLICATE_GEOMETRY", "handles": handles, "severity": "warning"}
            )

    geometry = [primitive for primitive in primitives if primitive.kind != "dimension"]
    dimensions = [primitive for primitive in primitives if primitive.kind == "dimension"]
    text_boxes = [
        (dimension, _dimension_text_box(dimension, default_text_height)) for dimension in dimensions
    ]
    text_boxes = [(dimension, box) for dimension, box in text_boxes if box is not None]
    minimum_gap = max(default_text_height * 0.35, 0.5)
    for index, (dimension, box) in enumerate(text_boxes):
        for other, other_box in text_boxes[index + 1 :]:
            if _rectangle_overlap(box, other_box, minimum_gap):
                warnings.append(
                    {
                        "code": "DIMENSION_TEXT_OVERLAP",
                        "handles": [dimension.handle, other.handle],
                        "severity": "error",
                    }
                )
        colliding = [item.handle for item in geometry if _geometry_hits_box(item, box)]
        if colliding:
            warnings.append(
                {
                    "code": "DIMENSION_TEXT_GEOMETRY_COLLISION",
                    "handles": [dimension.handle, *colliding[:8]],
                    "severity": "error",
                }
            )

    if geometry and text_boxes:
        geometry_bounds = _bounds(geometry)
        maximum_distance = max(default_text_height * 8.0, 10.0)
        for dimension, _box in text_boxes:
            distance = _distance_to_box(dimension.text_position, geometry_bounds)
            if distance > maximum_distance:
                warnings.append(
                    {
                        "code": "DIMENSION_TOO_FAR",
                        "handles": [dimension.handle],
                        "severity": "warning",
                        "distance": round(distance, 6),
                        "maximum_distance": round(maximum_distance, 6),
                    }
                )
    return {
        "entity_count": len(primitives),
        "type_counts": dict(sorted(type_counts.items())),
        "layer_counts": dict(sorted(layer_counts.items())),
        "warning_count": len(warnings),
        "warnings": warnings,
        "dimension_layout_passed": not any(
            warning.get("code", "").startswith("DIMENSION_") for warning in warnings
        ),
    }


def compare_expected_manifest(
    primitives: list[Primitive], manifest: dict[str, Any] | None
) -> dict[str, Any]:
    """Compare actual task geometry with a source-derived completeness manifest."""
    manifest = manifest or {}
    tolerance = max(float(manifest.get("tolerance", 1e-6)), 1e-9)
    checks: list[dict[str, Any]] = []
    counts = Counter(primitive.kind for primitive in primitives)

    for kind, expected in dict(manifest.get("minimum_counts") or {}).items():
        actual = counts[str(kind).lower()]
        expected_value = int(expected)
        checks.append(
            {
                "check": f"minimum_count:{kind}",
                "expected": expected_value,
                "actual": actual,
                "passed": actual >= expected_value,
            }
        )

    segments = [
        (primitive.handle, segment_start, segment_end)
        for primitive in primitives
        for segment_start, segment_end in _segments(primitive)
    ]
    for index, required in enumerate(manifest.get("required_segments") or []):
        start, end = _point(required.get("start")), _point(required.get("end"))
        local_tolerance = max(float(required.get("tolerance", tolerance)), tolerance)
        matched_handle = ""
        if start and end:
            for handle, actual_start, actual_end in segments:
                direct = (
                    math.dist(start, actual_start) <= local_tolerance
                    and math.dist(end, actual_end) <= local_tolerance
                )
                reverse = (
                    math.dist(start, actual_end) <= local_tolerance
                    and math.dist(end, actual_start) <= local_tolerance
                )
                if direct or reverse:
                    matched_handle = handle
                    break
        checks.append(
            {
                "check": f"required_segment:{index}",
                "expected": {"start": start, "end": end},
                "actual": matched_handle or None,
                "passed": bool(matched_handle),
            }
        )

    circles = [primitive for primitive in primitives if primitive.kind == "circle"]
    for index, required in enumerate(manifest.get("required_circles") or []):
        center = _point(required.get("center"))
        radius = required.get("radius")
        local_tolerance = max(float(required.get("tolerance", tolerance)), tolerance)
        matched_handle = ""
        if center and isinstance(radius, (int, float)):
            for circle in circles:
                if (
                    math.dist(center, circle.points[0]) <= local_tolerance
                    and abs(float(radius) - circle.radius) <= local_tolerance
                ):
                    matched_handle = circle.handle
                    break
        checks.append(
            {
                "check": f"required_circle:{index}",
                "expected": {"center": center, "radius": radius},
                "actual": matched_handle or None,
                "passed": bool(matched_handle),
            }
        )

    annotations = [primitive for primitive in primitives if primitive.label.strip()]
    for index, required in enumerate(manifest.get("required_annotations") or []):
        if isinstance(required, str):
            required = {"text": required}
        expected_text = str(required.get("text") or "").strip()
        match_mode = str(required.get("match") or "exact").strip().lower()
        expected_layer = str(required.get("layer") or "").strip().casefold()
        minimum = max(int(required.get("minimum_count", 1)), 1)
        bounds = required.get("bounds") or []
        normalized_expected = " ".join(expected_text.casefold().split())
        matched_handles: list[str] = []
        for annotation in annotations:
            normalized_actual = " ".join(annotation.label.casefold().split())
            text_matches = (
                normalized_expected in normalized_actual
                if match_mode == "contains"
                else normalized_actual == normalized_expected
            )
            layer_matches = not expected_layer or annotation.layer.casefold() == expected_layer
            bounds_match = True
            if isinstance(bounds, list) and len(bounds) == 4:
                point = annotation.text_position or annotation.points[-1]
                min_x, min_y, max_x, max_y = map(float, bounds)
                bounds_match = min_x <= point[0] <= max_x and min_y <= point[1] <= max_y
            if text_matches and layer_matches and bounds_match:
                matched_handles.append(annotation.handle)
        checks.append(
            {
                "check": f"required_annotation:{index}",
                "expected": {
                    "text": expected_text,
                    "match": match_mode,
                    "layer": expected_layer or None,
                    "minimum_count": minimum,
                    "bounds": bounds or None,
                },
                "actual": matched_handles,
                "passed": bool(normalized_expected) and len(matched_handles) >= minimum,
            }
        )

    for index, region in enumerate(manifest.get("view_regions") or []):
        bounds = region.get("bounds") or []
        inside = []
        if isinstance(bounds, list) and len(bounds) == 4:
            min_x, min_y, max_x, max_y = map(float, bounds)
            for primitive in primitives:
                points = _sample_points(primitive)
                center_x = sum(point[0] for point in points) / len(points)
                center_y = sum(point[1] for point in points) / len(points)
                if min_x <= center_x <= max_x and min_y <= center_y <= max_y:
                    inside.append(primitive)
        minimum = int(region.get("minimum_entities", 1))
        required_types = {str(item).lower() for item in region.get("required_types") or []}
        actual_types = {primitive.kind for primitive in inside}
        passed = len(inside) >= minimum and required_types.issubset(actual_types)
        checks.append(
            {
                "check": f"view_region:{region.get('name') or index}",
                "expected": {
                    "minimum_entities": minimum,
                    "required_types": sorted(required_types),
                },
                "actual": {
                    "entity_count": len(inside),
                    "types": sorted(actual_types),
                },
                "passed": passed,
            }
        )

    passed = None if not manifest else bool(checks) and all(check["passed"] for check in checks)
    return {
        "provided": bool(manifest),
        "passed": passed,
        "check_count": len(checks),
        "failed_count": sum(not check["passed"] for check in checks),
        "checks": checks,
    }


def _style(layer: str, approximate: bool) -> tuple[str, tuple[int, int, int], bool]:
    name = layer.upper()
    if approximate or "UNCERTAIN" in name:
        return "#ff9f1c", (255, 159, 28), True
    if "DIM" in name:
        return "#ff4fd8", (255, 79, 216), False
    if "CENTER" in name:
        return "#4dd0e1", (77, 208, 225), True
    if "HIDDEN" in name:
        return "#ffd166", (255, 209, 102), True
    if "HATCH" in name:
        return "#7bd389", (123, 211, 137), False
    return "#f4f7fb", (244, 247, 251), False


def _output_root() -> Path:
    root = Path(os.environ.get("MULTICAD_AUDIT_OUTPUT_ROOT", str(DEFAULT_AUDIT_ROOT)))
    if str(root).startswith("\\\\"):
        raise ValueError("Network audit output roots are not allowed")
    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_task_name(task_id: str) -> str:
    name = _SAFE_NAME.sub("_", task_id.strip())[:120].strip("._")
    if not name:
        raise ValueError("task_id does not contain a safe file name")
    return name


def _transform(bounds: tuple[float, float, float, float], width: int, height: int, padding: int):
    min_x, min_y, max_x, max_y = bounds
    header = 82
    scale = min(
        (width - 2 * padding) / (max_x - min_x), (height - header - 2 * padding) / (max_y - min_y)
    )

    def apply(point: tuple[float, float]) -> tuple[float, float]:
        x = padding + (point[0] - min_x) * scale
        y = height - padding - (point[1] - min_y) * scale
        return x, y

    return apply, scale, header


def _svg_render(
    primitives: list[Primitive],
    bounds: tuple[float, float, float, float],
    width: int,
    height: int,
    title: str,
) -> str:
    transform, scale, _header = _transform(bounds, width, height, 36)
    parts = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}">'
        ),
        '<rect width="100%" height="100%" fill="#111820"/>',
        (
            '<text x="28" y="34" fill="#ffffff" font-family="sans-serif" '
            f'font-size="20">{html.escape(title)}</text>'
        ),
        (
            '<text x="28" y="59" fill="#9fb3c8" font-family="sans-serif" '
            'font-size="13">OFF-SCREEN CAD AUDIT · no window capture</text>'
        ),
    ]
    for primitive in primitives:
        color, _rgb, dashed = _style(primitive.layer, primitive.approximate)
        dash = ' stroke-dasharray="10 7"' if dashed else ""
        common = f'stroke="{color}" stroke-width="1.7" fill="none"{dash}'
        if primitive.kind == "line":
            (x1, y1), (x2, y2) = map(transform, primitive.points)
            parts.append(
                f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" {common}/>'
            )
        elif primitive.kind == "circle":
            x, y = transform(primitive.points[0])
            parts.append(
                f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{primitive.radius * scale:.2f}" {common}/>'
            )
        elif primitive.kind == "arc":
            points = [transform(point) for point in _sample_points(primitive)]
            data = " ".join(
                ("M" if index == 0 else "L") + f" {x:.2f} {y:.2f}"
                for index, (x, y) in enumerate(points)
            )
            parts.append(f'<path d="{data}" {common}/>')
        elif primitive.kind == "polyline":
            points = [transform(point) for point in primitive.points]
            if primitive.closed and points[0] != points[-1]:
                points.append(points[0])
            data = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
            parts.append(f'<polyline points="{data}" {common}/>')
        elif primitive.kind == "dimension":
            points = [transform(point) for point in primitive.points]
            if len(points) >= 2:
                data = " ".join(f"{x:.2f},{y:.2f}" for x, y in points[:2])
                parts.append(f'<polyline points="{data}" {common}/>')
            text_point = primitive.text_position or primitive.points[-1]
            x, y = transform(text_point)
            parts.append(
                f'<text x="{x + 4:.2f}" y="{y - 4:.2f}" fill="{color}" '
                f'font-family="sans-serif" font-size="13">'
                f"{html.escape(primitive.label)}</text>"
            )
    parts.append("</svg>")
    return "\n".join(parts)


def _draw_dashed(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    fill: tuple[int, int, int],
    width: int = 2,
) -> None:
    for start, end in zip(points, points[1:]):
        length = math.dist(start, end)
        if length <= 0:
            continue
        dx, dy = (end[0] - start[0]) / length, (end[1] - start[1]) / length
        cursor = 0.0
        while cursor < length:
            finish = min(cursor + 10.0, length)
            draw.line(
                [
                    (start[0] + dx * cursor, start[1] + dy * cursor),
                    (start[0] + dx * finish, start[1] + dy * finish),
                ],
                fill=fill,
                width=width,
            )
            cursor += 17.0


def _png_render(
    primitives: list[Primitive],
    bounds: tuple[float, float, float, float],
    width: int,
    height: int,
    title: str,
) -> Image.Image:
    image = Image.new("RGB", (width, height), (17, 24, 32))
    draw = ImageDraw.Draw(image)
    font = _audit_font()
    draw.text((28, 22), title, fill=(255, 255, 255), font=font)
    draw.text((28, 46), "OFF-SCREEN CAD AUDIT - no window capture", fill=(159, 179, 200), font=font)
    transform, scale, _header = _transform(bounds, width, height, 36)
    for primitive in primitives:
        _hex, color, dashed = _style(primitive.layer, primitive.approximate)
        if primitive.kind == "line":
            points = [transform(point) for point in primitive.points]
        elif primitive.kind == "circle":
            x, y = transform(primitive.points[0])
            r = primitive.radius * scale
            draw.ellipse((x - r, y - r, x + r, y + r), outline=color, width=2)
            continue
        elif primitive.kind == "arc":
            points = [transform(point) for point in _sample_points(primitive)]
        elif primitive.kind == "polyline":
            points = [transform(point) for point in primitive.points]
            if primitive.closed and points[0] != points[-1]:
                points.append(points[0])
        elif primitive.kind == "dimension":
            points = [transform(point) for point in primitive.points]
            if len(points) >= 2:
                draw.line(points[:2], fill=color, width=1)
            text_point = primitive.text_position or primitive.points[-1]
            x, y = transform(text_point)
            draw.text((x + 4, y - 14), primitive.label, fill=color, font=font)
            continue
        else:
            continue
        if dashed:
            _draw_dashed(draw, points, color)
        else:
            draw.line(points, fill=color, width=2)
    return image


def _source_preview(source_path: str, page: int, target_size: tuple[int, int]) -> Image.Image:
    source = _validated_source(source_path)
    if source.suffix.lower() == ".pdf":
        try:
            import fitz
        except ImportError as exc:  # pragma: no cover - dependency-specific
            raise RuntimeError("PDF comparison requires PyMuPDF") from exc
        with fitz.open(source) as document:
            if page < 1 or page > len(document):
                raise ValueError(f"source_page must be between 1 and {len(document)}")
            pixmap = document[page - 1].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            image = Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)
    else:
        with Image.open(source) as opened:
            image = opened.convert("RGB")
    return ImageOps.contain(image, target_size)


def _comparison_image(source: Image.Image, cad: Image.Image) -> Image.Image:
    panel_width = max(source.width, cad.width)
    panel_height = max(source.height, cad.height)
    result = Image.new("RGB", (panel_width * 2 + 36, panel_height + 54), "white")
    source_x = (panel_width - source.width) // 2
    cad_x = panel_width + 36 + (panel_width - cad.width) // 2
    result.paste(source, (source_x, 44))
    result.paste(cad, (cad_x, 44))
    draw = ImageDraw.Draw(result)
    font = _audit_font()
    draw.text((12, 14), "SOURCE / SOURCE DRAWING", fill="black", font=font)
    draw.text((panel_width + 48, 14), "ACTUAL CAD TASK / ACTUAL CAD", fill="black", font=font)
    draw.line((panel_width + 18, 0, panel_width + 18, result.height), fill=(180, 180, 180), width=2)
    return result


def render_task_audit(
    task: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    width: int = 1600,
    height: int = 1000,
    expected_manifest: dict[str, Any] | None = None,
    source_path: str = "",
    source_page: int = 1,
) -> dict[str, Any]:
    """Render one task to local PNG/SVG and return machine-readable checks."""
    width = max(640, min(int(width), 4096))
    height = max(480, min(int(height), 4096))
    primitives = normalize_entities(records)
    bounds = _bounds(primitives)
    audit = audit_primitives(primitives)
    manifest_result = compare_expected_manifest(primitives, expected_manifest)
    task_id = str(task.get("task_id") or "")
    safe_name = _safe_task_name(task_id)
    output_dir = _output_root() / safe_name
    output_dir.mkdir(parents=True, exist_ok=True)
    svg_path = output_dir / "audit.svg"
    png_path = output_dir / "audit.png"
    title = f"{task.get('task_name') or task_id} [{task_id}]"
    svg_path.write_text(_svg_render(primitives, bounds, width, height, title), encoding="utf-8")
    cad_image = _png_render(primitives, bounds, width, height, title)
    cad_image.save(png_path, format="PNG")
    result = {
        "success": True,
        "task_id": task_id,
        "drawing_name": task.get("drawing_name", ""),
        "background_safe": True,
        "window_capture_used": False,
        "render_source": "fresh AutoCAD COM entity data",
        "png_path": str(png_path),
        "svg_path": str(svg_path),
        "pixel_size": [width, height],
        "world_bounds": {
            "min_x": bounds[0],
            "min_y": bounds[1],
            "max_x": bounds[2],
            "max_y": bounds[3],
        },
        "audit": audit,
        "manifest_comparison": manifest_result,
    }
    if source_path:
        source = _source_preview(source_path, int(source_page), (width, height))
        comparison_path = output_dir / "source_vs_cad.png"
        _comparison_image(source, cad_image).save(comparison_path, format="PNG")
        result["source_path"] = str(_validated_source(source_path))
        result["source_page"] = int(source_page)
        result["comparison_path"] = str(comparison_path)
    return result
