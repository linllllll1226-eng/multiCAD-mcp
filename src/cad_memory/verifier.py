"""Post-execution verification against actual AutoCAD COM entities."""

from __future__ import annotations

import math
from collections.abc import Mapping
from numbers import Real
from typing import Any

from .models import DrawingPlan, EntityPlan


def _safe_get(entity: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(entity, name)
    except Exception:
        return default


def _serializable(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Real):
        return float(value)
    if isinstance(value, Mapping):
        return {str(key): _serializable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serializable(item) for item in value]
    try:
        return [_serializable(item) for item in value]
    except (TypeError, ValueError):
        try:
            return float(value)
        except (TypeError, ValueError):
            return str(value)


def _as_sequence(value: Any) -> list[Any] | None:
    """Return a COM/Python sequence as a list without treating text as points."""
    if value is None or isinstance(value, (str, bytes, bytearray, Mapping)):
        return None
    if isinstance(value, (list, tuple)):
        return list(value)
    try:
        return list(value)
    except (TypeError, ValueError):
        return None


def _normalize_point(value: Any) -> tuple[float, float, float] | None:
    """Normalize a 2D or 3D point to an XYZ tuple."""
    values = _as_sequence(value)
    if values is None or len(values) not in {2, 3}:
        return None
    try:
        numbers = [float(item) for item in values]
    except (TypeError, ValueError):
        return None
    if len(numbers) == 2:
        numbers.append(0.0)
    return numbers[0], numbers[1], numbers[2]


def _normalize_points(
    value: Any, *, stride: int | None = None
) -> list[tuple[float, float, float]] | None:
    """Normalize nested or flattened 2D/3D point sequences."""
    values = _as_sequence(value)
    if values is None:
        return None
    if not values:
        return []

    nested = [_as_sequence(item) for item in values]
    if all(item is not None for item in nested):
        points: list[tuple[float, float, float]] = []
        for item in nested:
            point = _normalize_point(item)
            if point is None:
                return None
            points.append(point)
        return points
    if any(item is not None for item in nested):
        return None

    if stride is None:
        if len(values) % 3 == 0:
            stride = 3
        elif len(values) % 2 == 0:
            stride = 2
        else:
            return None
    if stride not in {2, 3} or len(values) % stride:
        return None
    points = []
    for index in range(0, len(values), stride):
        point = _normalize_point(values[index : index + stride])
        if point is None:
            return None
        points.append(point)
    return points


def _polyline_stride(object_type: Any) -> int:
    """Return the coordinate stride used by AutoCAD's polyline classes."""
    lowered = str(object_type or "").lower()
    return 3 if "2dpolyline" in lowered or "3dpolyline" in lowered else 2


def _rectangle_vertices(coordinates: Mapping[str, Any]) -> list[list[float]] | None:
    """Expand opposite rectangle corners into the ordered closed polyline."""
    corner1 = _normalize_point(coordinates.get("corner1"))
    corner2 = _normalize_point(coordinates.get("corner2"))
    if corner1 is None or corner2 is None:
        return None
    x1, y1, z1 = corner1
    x2, y2, z2 = corner2
    return [
        [x1, y1, z1],
        [x2, y1, z1],
        [x2, y2, z2],
        [x1, y2, z2],
        [x1, y1, z1],
    ]


_POINT_PROPERTIES = frozenset(
    {
        "start",
        "end",
        "center",
        "position",
        "text_position",
        "ext_line1_point",
        "ext_line2_point",
        "xline1_point",
        "xline2_point",
        "chord_point",
        "far_chord_point",
    }
)
_ANGLE_PROPERTIES = frozenset({"start_angle", "end_angle"})


def read_entity_state(entity: Any) -> dict[str, Any]:
    """Read geometry and dimension properties without changing the entity."""
    state: dict[str, Any] = {}
    for output, prop in {
        "handle": "Handle",
        "object_type": "ObjectName",
        "layer": "Layer",
        "linetype": "Linetype",
        "closed": "Closed",
        "start": "StartPoint",
        "end": "EndPoint",
        "center": "Center",
        "radius": "Radius",
        "length": "Length",
        "start_angle": "StartAngle",
        "end_angle": "EndAngle",
        "measurement": "Measurement",
        "text_height": "TextHeight",
        "text_override": "TextOverride",
        "text_position": "TextPosition",
        "position": "InsertionPoint",
        "text": "TextString",
        "ext_line1_point": "ExtLine1Point",
        "ext_line2_point": "ExtLine2Point",
        "xline1_point": "XLine1Point",
        "xline2_point": "XLine2Point",
        "chord_point": "ChordPoint",
        "far_chord_point": "FarChordPoint",
        "coordinates": "Coordinates",
    }.items():
        value = _safe_get(entity, prop)
        if value is not None:
            # AutoCAD exposes arc angles in radians while plans use degrees.
            if output in _ANGLE_PROPERTIES:
                try:
                    value = math.degrees(float(value))
                except (TypeError, ValueError):
                    pass
            state[output] = _serializable(value)
    for extension_name, rotated_name in (
        ("ext_line1_point", "xline1_point"),
        ("ext_line2_point", "xline2_point"),
    ):
        if extension_name not in state and rotated_name in state:
            state[extension_name] = state[rotated_name]
    if "text_height" not in state and "text" in str(state.get("object_type", "")).lower():
        height = _safe_get(entity, "Height")
        if height is not None:
            state["text_height"] = _serializable(height)
    if "radius" in state:
        state["diameter"] = 2.0 * float(state["radius"])
    coordinates = state.get("coordinates")
    vertices = _normalize_points(
        coordinates,
        stride=_polyline_stride(state.get("object_type")),
    )
    if vertices:
        state["vertices"] = [list(point) for point in vertices]
        state["vertex_count"] = len(vertices)
        xs = [point[0] for point in vertices]
        ys = [point[1] for point in vertices]
        state["width"] = max(xs) - min(xs)
        state["height"] = max(ys) - min(ys)
    fill_values = [
        _safe_get(entity, "TextFill"),
        _safe_get(entity, "UseBackgroundColor"),
        _safe_get(entity, "BackgroundFill"),
    ]
    state["background_fill"] = any(value is True or value == 1 for value in fill_values)
    return state


def _expected_object_type(entity_type: str) -> str | list[str]:
    expected_types: dict[str, str | list[str]] = {
        "line": "AcDbLine",
        "text": "AcDbText",
        "rectangle": ["AcDbPolyline", "AcDb2dPolyline"],
        "polyline": ["AcDbPolyline", "AcDb2dPolyline"],
        "circle": "AcDbCircle",
        "arc": "AcDbArc",
        "aligned_dimension": "AcDbAlignedDimension",
        # The guarded executor currently routes both linear and aligned
        # dimensions through adapter.add_dimension(), which may return an
        # AcDbAlignedDimension depending on the CAD COM implementation.
        "linear_dimension": ["AcDbRotatedDimension", "AcDbAlignedDimension"],
        "diametric_dimension": "AcDbDiametricDimension",
        "radial_dimension": "AcDbRadialDimension",
    }
    return expected_types.get(entity_type.lower(), entity_type)


def _angle_error(target: Any, actual: Any) -> float:
    """Compare degree angles on a circle, treating 0 and 360 as equivalent."""
    try:
        target_angle = float(target) % 360.0
        actual_angle = float(actual) % 360.0
    except (TypeError, ValueError):
        return math.inf
    return abs((actual_angle - target_angle + 180.0) % 360.0 - 180.0)


def _numeric_error(target: Any, actual: Any, *, property_name: str = "") -> float | None:
    if property_name in _POINT_PROPERTIES:
        expected_point = _normalize_point(target)
        actual_point = _normalize_point(actual)
        if expected_point is None or actual_point is None:
            return math.inf
        return max(
            abs(expected - observed) for expected, observed in zip(expected_point, actual_point)
        )
    if property_name == "vertices":
        expected_points = _normalize_points(target)
        actual_points = _normalize_points(actual)
        if expected_points is None or actual_points is None:
            return math.inf
        if len(expected_points) != len(actual_points):
            return math.inf
        return max(
            (
                max(
                    abs(expected - observed)
                    for expected, observed in zip(expected_point, actual_point)
                )
                for expected_point, actual_point in zip(expected_points, actual_points)
            ),
            default=0.0,
        )
    if property_name in _ANGLE_PROPERTIES:
        return _angle_error(target, actual)
    if isinstance(target, Real) and not isinstance(target, bool):
        if isinstance(actual, Real) and not isinstance(actual, bool):
            return abs(float(target) - float(actual))
        return math.inf
    if isinstance(target, (list, tuple)):
        target_values = _as_sequence(target)
        actual_values = _as_sequence(actual)
        if target_values is None or actual_values is None:
            return math.inf
        if len(target_values) != len(actual_values):
            return math.inf
        errors = [
            _numeric_error(expected, observed)
            for expected, observed in zip(target_values, actual_values)
        ]
        if any(error is None for error in errors):
            return None
        numeric_errors = [error for error in errors if error is not None]
        return max(numeric_errors, default=0.0)
    return None


class PostExecutionVerifier:
    """Compare planned targets with freshly read entity properties."""

    def verify(self, adapter: Any, plan: DrawingPlan, handles: list[str]) -> dict[str, Any]:
        """Read each handle from CAD and compare it with the matching plan item."""
        if len(handles) != len(plan.entities):
            return {
                "passed": False,
                "rows": [],
                "errors": ["Handle count does not match planned entity count"],
            }
        document = adapter._get_document("cad_verify_execution")
        rows: list[dict[str, Any]] = []
        actual_entities: list[dict[str, Any]] = []
        errors: list[str] = []
        for index, (target, handle) in enumerate(zip(plan.entities, handles)):
            try:
                actual = read_entity_state(document.HandleToObject(handle))
                if str(actual.get("linetype", "")).lower() == "bylayer":
                    try:
                        layer = document.Layers.Item(actual["layer"])
                        actual["effective_linetype"] = str(layer.Linetype)
                    except Exception:
                        pass
                actual_entities.append(actual)
                rows.extend(self._compare_entity(index, target, actual, plan.tolerance))
            except Exception as exc:
                errors.append(f"entity[{index}] {handle}: {exc}")
        mismatches = [row for row in rows if not row["passed"]]
        errors.extend(
            "entity[{entity_index}] {property}: expected={target!r}, actual={actual!r}, "
            "error={error!r}".format(**row)
            for row in mismatches
        )
        passed = not errors and all(row["passed"] for row in rows)
        return {
            "passed": passed,
            "columns": ["target", "actual", "error", "passed"],
            "rows": rows,
            "mismatches": mismatches,
            "actual_entities": actual_entities,
            "errors": errors,
        }

    def _compare_entity(
        self, index: int, target: EntityPlan, actual: dict[str, Any], tolerance: float
    ) -> list[dict[str, Any]]:
        checks: dict[str, Any] = {
            "layer": target.layer,
            "linetype": target.linetype,
            "object_type": _expected_object_type(target.entity_type),
        }
        if target.linetype.lower() == "bylayer":
            if target.layer.upper() == "AI_PREVIEW_CENTER":
                checks["effective_linetype"] = ["CENTER", "CENTER2", "CENTERX2"]
            elif target.layer.upper() == "AI_PREVIEW_HIDDEN":
                checks["effective_linetype"] = ["HIDDEN", "HIDDEN2", "HIDDENX2"]
        kind = target.entity_type.lower()
        if kind == "line":
            checks.update(start=target.coordinates["start"], end=target.coordinates["end"])
            a, b = target.coordinates["start"], target.coordinates["end"]
            checks["length"] = math.dist(a[:2], b[:2])
        elif kind == "text":
            checks.update(
                position=target.coordinates["position"],
                text=target.text_override,
                text_height=target.dimensions["height"],
            )
        elif kind == "rectangle":
            checks["closed"] = True
            expected_vertices = _rectangle_vertices(target.coordinates)
            checks["vertices"] = expected_vertices
            checks["vertex_count"] = (
                len(expected_vertices) if expected_vertices is not None else None
            )
            if "width" in target.dimensions:
                checks["width"] = target.dimensions["width"]
            if "height" in target.dimensions:
                checks["height"] = target.dimensions["height"]
        elif kind == "polyline":
            points = target.coordinates.get("points")
            checks["vertices"] = points
            if isinstance(points, (list, tuple)):
                checks["vertex_count"] = len(points)
            else:
                checks["vertex_count"] = None
            if "closed" in target.dimensions:
                checks["closed"] = bool(target.dimensions["closed"])
        elif kind == "circle":
            checks.update(center=target.coordinates["center"], radius=target.dimensions["radius"])
            checks["diameter"] = 2.0 * target.dimensions["radius"]
        elif kind == "arc":
            checks.update(
                center=target.coordinates["center"],
                radius=target.dimensions["radius"],
                start_angle=target.dimensions.get("start_angle"),
                end_angle=target.dimensions.get("end_angle"),
            )
        elif kind in {
            "aligned_dimension",
            "linear_dimension",
            "diametric_dimension",
            "radial_dimension",
        }:
            if "measurement" in target.dimensions:
                checks["measurement"] = target.dimensions["measurement"]
            checks["text_override"] = target.text_override or ""
            checks["background_fill"] = bool(target.background_fill)
            if "text_height" in target.dimensions:
                checks["text_height"] = target.dimensions["text_height"]
            if "text_position" in target.coordinates:
                checks["text_position"] = target.coordinates["text_position"]
            if target.operation != "layout_only":
                if kind in {"aligned_dimension", "linear_dimension"}:
                    checks["ext_line1_point"] = target.coordinates.get("start")
                    checks["ext_line2_point"] = target.coordinates.get("end")
                elif kind == "diametric_dimension":
                    checks["chord_point"] = target.coordinates.get("chord_point")
                    checks["far_chord_point"] = target.coordinates.get("far_chord_point")
                elif kind == "radial_dimension":
                    checks["center"] = target.coordinates.get("center")
                    checks["chord_point"] = target.coordinates.get("chord_point")

        rows = []
        for name, target_value in checks.items():
            actual_value = actual.get(name)
            error = _numeric_error(target_value, actual_value, property_name=name)
            if name in {"object_type", "effective_linetype"} and isinstance(target_value, list):
                passed = str(actual_value).lower() in {value.lower() for value in target_value}
            elif error is not None:
                passed = error <= tolerance
            else:
                passed = str(target_value).lower() == str(actual_value).lower()
            rows.append(
                {
                    "entity_index": index,
                    "property": name,
                    "target": target_value,
                    "actual": actual_value,
                    "error": error,
                    "passed": passed,
                }
            )
        return rows
