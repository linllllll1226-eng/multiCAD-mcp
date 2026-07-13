"""Post-execution verification against actual AutoCAD COM entities."""

from __future__ import annotations

import math
from typing import Any

from .models import DrawingPlan, EntityPlan


def _safe_get(entity: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(entity, name)
    except Exception:
        return default


def _serializable(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return [float(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        return list(value)
    except Exception:
        return str(value)


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
        "measurement": "Measurement",
        "text_override": "TextOverride",
        "text_position": "TextPosition",
    }.items():
        value = _safe_get(entity, prop)
        if value is not None:
            state[output] = _serializable(value)
    if "radius" in state:
        state["diameter"] = 2.0 * float(state["radius"])
    fill_values = [
        _safe_get(entity, "TextFill"),
        _safe_get(entity, "UseBackgroundColor"),
        _safe_get(entity, "BackgroundFill"),
    ]
    state["background_fill"] = any(value is True or value == 1 for value in fill_values)
    return state


def _expected_object_type(entity_type: str) -> str:
    return {
        "line": "AcDbLine",
        "rectangle": "AcDbPolyline",
        "polyline": "AcDbPolyline",
        "circle": "AcDbCircle",
        "arc": "AcDbArc",
        "aligned_dimension": "AcDbAlignedDimension",
        "linear_dimension": "AcDbRotatedDimension",
        "diametric_dimension": "AcDbDiametricDimension",
        "radial_dimension": "AcDbRadialDimension",
    }.get(entity_type.lower(), entity_type)


def _numeric_error(target: Any, actual: Any) -> float | None:
    if isinstance(target, (int, float)) and isinstance(actual, (int, float)):
        return abs(float(target) - float(actual))
    if isinstance(target, (list, tuple)) and isinstance(actual, (list, tuple)):
        if len(target) != len(actual):
            return math.inf
        return max(abs(float(a) - float(b)) for a, b in zip(target, actual))
    return None


class PostExecutionVerifier:
    """Compare planned targets with freshly read entity properties."""

    def verify(
        self, adapter: Any, plan: DrawingPlan, handles: list[str]
    ) -> dict[str, Any]:
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
                actual_entities.append(actual)
                rows.extend(self._compare_entity(index, target, actual, plan.tolerance))
            except Exception as exc:
                errors.append(f"entity[{index}] {handle}: {exc}")
        passed = not errors and all(row["passed"] for row in rows)
        return {
            "passed": passed,
            "columns": ["target", "actual", "error", "passed"],
            "rows": rows,
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
        kind = target.entity_type.lower()
        if kind == "line":
            checks.update(
                start=target.coordinates["start"], end=target.coordinates["end"]
            )
            a, b = target.coordinates["start"], target.coordinates["end"]
            checks["length"] = math.dist(a[:2], b[:2])
        elif kind == "rectangle":
            checks["closed"] = True
        elif kind == "polyline" and "closed" in target.dimensions:
            checks["closed"] = bool(target.dimensions["closed"])
        elif kind == "circle":
            checks.update(
                center=target.coordinates["center"], radius=target.dimensions["radius"]
            )
            checks["diameter"] = 2.0 * target.dimensions["radius"]
        elif kind == "arc":
            checks.update(
                center=target.coordinates["center"], radius=target.dimensions["radius"]
            )
        elif kind in {
            "aligned_dimension",
            "linear_dimension",
            "diametric_dimension",
            "radial_dimension",
        }:
            if "measurement" in target.dimensions:
                checks["measurement"] = target.dimensions["measurement"]
            checks["text_override"] = ""
            checks["background_fill"] = False

        rows = []
        for name, target_value in checks.items():
            actual_value = actual.get(name)
            error = _numeric_error(target_value, actual_value)
            if error is not None:
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
