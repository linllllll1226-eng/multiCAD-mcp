"""Pre-execution validation for structured CAD drawing plans."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from .models import ConstraintSpec, DrawingPlan, EntityPlan

PREVIEW_LAYERS = {
    "AI_PREVIEW_OUTLINE",
    "AI_PREVIEW_CENTER",
    "AI_PREVIEW_HIDDEN",
    "AI_PREVIEW_HATCH",
    "AI_PREVIEW_DIM",
    "AI_UNCERTAIN",
}
DIMENSION_TYPES = {
    "aligned_dimension",
    "linear_dimension",
    "diametric_dimension",
    "radial_dimension",
}


@dataclass
class ValidationIssue:
    """One validation error or warning."""

    code: str
    message: str
    entity_index: int | None = None


@dataclass
class ValidationReport:
    """Structured result of all pre-execution checks."""

    passed: bool
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation."""
        return {
            "passed": self.passed,
            "errors": [asdict(issue) for issue in self.errors],
            "warnings": [asdict(issue) for issue in self.warnings],
            "checks": self.checks,
        }


def _point(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        x, y = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x) or not math.isfinite(y):
        return None
    return x, y


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


class PlanValidator:
    """Validate units, geometry, layers, constraints, and safety controls."""

    def validate(
        self,
        plan: DrawingPlan | dict[str, Any],
        *,
        available_layers: Iterable[str] | None = None,
    ) -> ValidationReport:
        """Validate a plan against supplied or declared layer names."""
        if not isinstance(plan, DrawingPlan):
            plan = DrawingPlan.model_validate(plan)
        report = ValidationReport(passed=False)
        layers = set(
            available_layers if available_layers is not None else plan.existing_layers
        )

        if not plan.unit or not plan.unit.strip():
            report.errors.append(
                ValidationIssue("unit_missing", "Drawing unit is not specified")
            )
        else:
            report.checks.append(f"unit={plan.unit}")

        if not plan.user_confirmed:
            report.errors.append(
                ValidationIssue(
                    "plan_not_confirmed",
                    "The drawing plan is not confirmed by the user",
                )
            )

        for index, entity in enumerate(plan.entities):
            self._validate_entity(plan, entity, index, layers, report)

        report.passed = not report.errors
        return report

    def _validate_entity(
        self,
        plan: DrawingPlan,
        entity: EntityPlan,
        index: int,
        layers: set[str],
        report: ValidationReport,
    ) -> None:
        kind = entity.entity_type.lower()
        if entity.layer not in layers:
            report.errors.append(
                ValidationIssue(
                    "layer_missing", f"Layer does not exist: {entity.layer}", index
                )
            )

        if (
            entity.dimension_source == "approximate_reference"
            and entity.layer != "AI_UNCERTAIN"
        ):
            report.errors.append(
                ValidationIssue(
                    "approximate_on_formal_layer",
                    "approximate_reference entities must use AI_UNCERTAIN",
                    index,
                )
            )

        if (
            plan.preview_mode
            and entity.operation == "create"
            and entity.layer not in PREVIEW_LAYERS
        ):
            report.errors.append(
                ValidationIssue(
                    "non_preview_layer",
                    f"Preview creation must use a preview layer, not {entity.layer}",
                    index,
                )
            )

        if entity.uncertain_items and entity.layer != "AI_UNCERTAIN":
            report.errors.append(
                ValidationIssue(
                    "unconfirmed_uncertain_geometry",
                    "Uncertain geometry cannot enter a formal layer",
                    index,
                )
            )

        if entity.operation == "delete" and not plan.allow_delete:
            report.errors.append(
                ValidationIssue(
                    "delete_not_confirmed", "Deletion is not explicitly allowed", index
                )
            )
        if entity.operation == "modify" and not plan.allow_overwrite:
            report.errors.append(
                ValidationIssue(
                    "overwrite_not_confirmed",
                    "Modification is not explicitly allowed",
                    index,
                )
            )
        if (
            entity.operation in {"delete", "modify", "layout_only"}
            and not entity.target_handles
        ):
            report.errors.append(
                ValidationIssue(
                    "target_missing", "The operation requires target_handles", index
                )
            )

        for name, value in entity.dimensions.items():
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                report.errors.append(
                    ValidationIssue(
                        "dimension_invalid", f"Dimension {name} is not finite", index
                    )
                )
            elif (
                name not in {"start_angle", "end_angle", "angle"} and float(value) <= 0
            ):
                report.errors.append(
                    ValidationIssue(
                        "dimension_nonpositive",
                        f"Dimension {name} must be positive",
                        index,
                    )
                )

        if entity.operation == "layout_only":
            if kind not in DIMENSION_TYPES:
                report.errors.append(
                    ValidationIssue(
                        "layout_target_invalid",
                        "layout_only is restricted to dimension objects",
                        index,
                    )
                )
            if _point(entity.coordinates.get("text_position")) is None:
                report.errors.append(
                    ValidationIssue(
                        "text_position_missing",
                        "layout_only requires a valid text_position",
                        index,
                    )
                )
        else:
            self._validate_required_geometry(kind, entity, index, report)
        self._validate_dimension_safety(kind, entity, index, report)
        for constraint in entity.constraints:
            self._validate_constraint(constraint, index, report)

    def _validate_required_geometry(
        self, kind: str, entity: EntityPlan, index: int, report: ValidationReport
    ) -> None:
        requirements = {
            "line": ("start", "end"),
            "rectangle": ("corner1", "corner2"),
            "circle": ("center",),
            "arc": ("center",),
            "polyline": ("points",),
            "aligned_dimension": ("start", "end"),
            "linear_dimension": ("start", "end"),
            "diametric_dimension": ("chord_point", "far_chord_point"),
            "radial_dimension": ("center", "chord_point"),
        }
        required = requirements.get(kind)
        if required is None:
            report.errors.append(
                ValidationIssue(
                    "entity_type_unsupported", f"Unsupported entity type: {kind}", index
                )
            )
            return
        for name in required:
            value = entity.coordinates.get(name)
            if name == "points":
                if (
                    not isinstance(value, list)
                    or len(value) < 2
                    or any(_point(p) is None for p in value)
                ):
                    report.errors.append(
                        ValidationIssue(
                            "coordinates_missing",
                            "Polyline requires at least two valid points",
                            index,
                        )
                    )
            elif _point(value) is None:
                report.errors.append(
                    ValidationIssue(
                        "coordinates_missing",
                        f"Missing or invalid coordinate: {name}",
                        index,
                    )
                )

        if kind == "circle" and entity.dimensions.get("radius", 0) <= 0:
            report.errors.append(
                ValidationIssue(
                    "circle_radius_invalid", "Circle radius must be positive", index
                )
            )
        if kind == "arc":
            if entity.dimensions.get("radius", 0) <= 0:
                report.errors.append(
                    ValidationIssue(
                        "arc_radius_invalid", "Arc radius must be positive", index
                    )
                )
            start = entity.dimensions.get("start_angle")
            end = entity.dimensions.get("end_angle")
            if (
                start is None
                or end is None
                or start == end
                or not (0 <= start < 360 and 0 <= end < 360)
            ):
                report.errors.append(
                    ValidationIssue(
                        "arc_angles_invalid",
                        "Arc angles must be distinct values in [0, 360)",
                        index,
                    )
                )

    def _validate_dimension_safety(
        self, kind: str, entity: EntityPlan, index: int, report: ValidationReport
    ) -> None:
        if kind not in DIMENSION_TYPES:
            return
        if entity.background_fill:
            report.errors.append(
                ValidationIssue(
                    "dimension_background_fill",
                    "Dimension background fill must be disabled",
                    index,
                )
            )
        override = entity.text_override or ""
        if (
            kind
            in {
                "diametric_dimension",
                "radial_dimension",
                "aligned_dimension",
                "linear_dimension",
            }
            and override
        ):
            report.errors.append(
                ValidationIssue(
                    "dimension_text_override",
                    "TextOverride must be empty by default",
                    index,
                )
            )
        if "ØØ" in override or "%%c%%c" in override.lower():
            report.errors.append(
                ValidationIssue("duplicate_diameter_prefix", "ØØ is not allowed", index)
            )
        if "RR" in override.upper():
            report.errors.append(
                ValidationIssue("duplicate_radius_prefix", "RR is not allowed", index)
            )
        if entity.operation == "layout_only" and any(
            key in entity.coordinates
            for key in ("start", "end", "center", "chord_point", "far_chord_point")
        ):
            report.errors.append(
                ValidationIssue(
                    "layout_changes_geometry",
                    "Dimension layout operations must not provide geometry coordinates",
                    index,
                )
            )

    def _validate_constraint(
        self, constraint: ConstraintSpec, index: int, report: ValidationReport
    ) -> None:
        data = constraint.data
        tolerance = constraint.tolerance
        try:
            if constraint.kind == "concentric":
                centers = [_point(p) for p in data.get("centers", [])]
                passed = (
                    len(centers) >= 2
                    and all(centers)
                    and all(
                        _distance(centers[0], center) <= tolerance
                        for center in centers[1:]
                    )
                )
            elif constraint.kind == "symmetry":
                axis = data.get("axis")
                axis_value = float(data.get("axis_value", 0))
                pairs = data.get("pairs", [])
                if axis == "x":
                    passed = bool(pairs) and all(
                        abs(float(a[0]) + float(b[0]) - 2 * axis_value) <= tolerance
                        and abs(float(a[1]) - float(b[1])) <= tolerance
                        for a, b in pairs
                    )
                elif axis == "y":
                    passed = bool(pairs) and all(
                        abs(float(a[1]) + float(b[1]) - 2 * axis_value) <= tolerance
                        and abs(float(a[0]) - float(b[0])) <= tolerance
                        for a, b in pairs
                    )
                else:
                    passed = False
            elif constraint.kind == "equal_distance":
                distances = [float(value) for value in data.get("distances", [])]
                passed = (
                    len(distances) >= 2 and max(distances) - min(distances) <= tolerance
                )
            elif constraint.kind == "dimension_chain":
                parts = [float(value) for value in data.get("parts", [])]
                total = float(data["total"])
                passed = bool(parts) and abs(sum(parts) - total) <= tolerance
            elif constraint.kind == "uniform_distribution":
                passed = self._uniform_distribution(data, tolerance)
            elif constraint.kind == "tangent":
                passed = self._tangent(data, tolerance)
            else:
                passed = False
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            passed = False
        if not passed:
            report.errors.append(
                ValidationIssue(
                    f"constraint_{constraint.kind}_failed",
                    f"Constraint failed: {constraint.kind}",
                    index,
                )
            )
        else:
            report.checks.append(
                f"entity[{index}] constraint {constraint.kind}: passed"
            )

    @staticmethod
    def _uniform_distribution(data: dict[str, Any], tolerance: float) -> bool:
        center = _point(data.get("center"))
        points = [_point(value) for value in data.get("points", [])]
        expected_angle = float(data.get("angle", 360 / len(points))) if points else 0
        if center is None or len(points) < 2 or any(point is None for point in points):
            return False
        radii = [_distance(center, point) for point in points]
        if max(radii) - min(radii) > tolerance:
            return False
        angles = sorted(
            (math.degrees(math.atan2(p[1] - center[1], p[0] - center[0])) % 360)
            for p in points
        )
        gaps = [
            (angles[(i + 1) % len(angles)] - angles[i]) % 360
            for i in range(len(angles))
        ]
        return all(abs(gap - expected_angle) <= tolerance for gap in gaps)

    @staticmethod
    def _tangent(data: dict[str, Any], tolerance: float) -> bool:
        center1 = _point(data.get("center1"))
        center2 = _point(data.get("center2"))
        if center1 and center2:
            r1, r2 = float(data["radius1"]), float(data["radius2"])
            distance = _distance(center1, center2)
            return (
                min(abs(distance - (r1 + r2)), abs(distance - abs(r1 - r2)))
                <= tolerance
            )
        line_start = _point(data.get("line_start"))
        line_end = _point(data.get("line_end"))
        center = _point(data.get("center"))
        if not line_start or not line_end or not center:
            return False
        radius = float(data["radius"])
        dx, dy = line_end[0] - line_start[0], line_end[1] - line_start[1]
        length = math.hypot(dx, dy)
        if length == 0:
            return False
        distance = (
            abs(
                dy * center[0]
                - dx * center[1]
                + line_end[0] * line_start[1]
                - line_end[1] * line_start[0]
            )
            / length
        )
        return abs(distance - radius) <= tolerance
