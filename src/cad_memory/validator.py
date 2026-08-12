"""Pre-execution validation for structured CAD drawing plans."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Literal

from .models import ConstraintSpec, DrawingPlan, EntityPlan
from .receipts import SUPPORTED_PLAN_UNITS, normalize_unit

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
GEOMETRY_TOLERANCE = 1e-9


@dataclass(frozen=True)
class DimensionField:
    """Typed validation rule for one entity-specific dimension field."""

    value_type: Literal["boolean", "finite", "positive", "angle"]
    required: bool = False


ENTITY_DIMENSION_SCHEMAS: dict[str, dict[str, DimensionField]] = {
    "line": {},
    "text": {
        "height": DimensionField("positive", required=True),
        "rotation": DimensionField("angle"),
    },
    "rectangle": {
        "width": DimensionField("positive"),
        "height": DimensionField("positive"),
        "area": DimensionField("positive"),
    },
    "circle": {"radius": DimensionField("positive", required=True)},
    "arc": {
        "radius": DimensionField("positive", required=True),
        "start_angle": DimensionField("angle", required=True),
        "end_angle": DimensionField("angle", required=True),
    },
    "polyline": {"closed": DimensionField("boolean")},
    "aligned_dimension": {
        "measurement": DimensionField("positive"),
        "offset": DimensionField("positive"),
        "text_height": DimensionField("positive"),
    },
    "linear_dimension": {
        "measurement": DimensionField("positive"),
        "offset": DimensionField("positive"),
        "text_height": DimensionField("positive"),
    },
    "diametric_dimension": {
        "diameter": DimensionField("positive"),
        "measurement": DimensionField("positive"),
        "leader_length": DimensionField("positive"),
        "text_height": DimensionField("positive"),
    },
    "radial_dimension": {
        "radius": DimensionField("positive"),
        "measurement": DimensionField("positive"),
        "leader_length": DimensionField("positive"),
        "text_height": DimensionField("positive"),
    },
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


def _point(value: Any) -> tuple[float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) not in {2, 3}:
        return None
    try:
        coordinates = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in coordinates):
        return None
    if len(coordinates) == 2:
        return coordinates[0], coordinates[1], 0.0
    return coordinates[0], coordinates[1], coordinates[2]


def _distance(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.dist(a, b)


class PlanValidator:
    """Validate units, geometry, layers, constraints, and safety controls."""

    def validate(
        self,
        plan: DrawingPlan | dict[str, Any],
        *,
        available_layers: Iterable[str] | None = None,
        drawing_unit: str | None = None,
    ) -> ValidationReport:
        """Validate a plan against supplied layers and the active drawing unit."""
        if not isinstance(plan, DrawingPlan):
            plan = DrawingPlan.model_validate(plan)
        report = ValidationReport(passed=False)
        layers = set(available_layers if available_layers is not None else plan.existing_layers)

        plan_unit = normalize_unit(plan.unit)
        active_unit = normalize_unit(drawing_unit)
        if plan_unit is None:
            report.errors.append(ValidationIssue("unit_missing", "Drawing unit is not specified"))
        elif plan_unit not in SUPPORTED_PLAN_UNITS:
            report.errors.append(
                ValidationIssue("unit_unsupported", f"Unsupported drawing unit: {plan.unit}")
            )
        else:
            report.checks.append(f"plan_unit={plan_unit}")
            if active_unit == "unitless":
                report.warnings.append(
                    ValidationIssue(
                        "drawing_unit_unitless",
                        "Active drawing INSUNITS is unitless; coordinates are "
                        "interpreted "
                        f"as {plan_unit} only because the plan explicitly says so",
                    )
                )
            elif active_unit is not None and active_unit != plan_unit:
                report.errors.append(
                    ValidationIssue(
                        "drawing_unit_mismatch",
                        f"Plan unit {plan_unit} does not match active drawing "
                        f"INSUNITS {active_unit}",
                    )
                )
            elif active_unit is not None:
                report.checks.append(f"drawing_unit={active_unit}")

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
                ValidationIssue("layer_missing", f"Layer does not exist: {entity.layer}", index)
            )

        if entity.dimension_source == "approximate_reference" and entity.layer != "AI_UNCERTAIN":
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
                ValidationIssue("delete_not_confirmed", "Deletion is not explicitly allowed", index)
            )
        if entity.operation == "modify" and not plan.allow_overwrite:
            report.errors.append(
                ValidationIssue(
                    "overwrite_not_confirmed",
                    "Modification is not explicitly allowed",
                    index,
                )
            )
        if entity.operation in {"delete", "modify", "layout_only"} and not entity.target_handles:
            report.errors.append(
                ValidationIssue("target_missing", "The operation requires target_handles", index)
            )

        self._validate_dimensions(kind, entity, index, report)

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
            "text": ("position",),
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

        self._validate_entity_geometry(kind, entity, index, report)

        if kind == "text":
            if not entity.text_override.strip():
                report.errors.append(
                    ValidationIssue("text_missing", "Text content must not be empty", index)
                )
            height = entity.dimensions.get("height")
            if (
                isinstance(height, bool)
                or not isinstance(height, (int, float))
                or not math.isfinite(float(height))
                or float(height) <= 0
            ):
                report.errors.append(
                    ValidationIssue("text_height_invalid", "Text height must be positive", index)
                )

        if kind == "circle":
            radius = entity.dimensions.get("radius")
            if (
                isinstance(radius, bool)
                or not isinstance(radius, (int, float))
                or not math.isfinite(float(radius))
                or float(radius) <= 0
            ):
                report.errors.append(
                    ValidationIssue(
                        "circle_radius_invalid", "Circle radius must be positive", index
                    )
                )
        if kind == "arc":
            radius = entity.dimensions.get("radius")
            if (
                isinstance(radius, bool)
                or not isinstance(radius, (int, float))
                or not math.isfinite(float(radius))
                or float(radius) <= 0
            ):
                report.errors.append(
                    ValidationIssue("arc_radius_invalid", "Arc radius must be positive", index)
                )

    def _validate_dimensions(
        self, kind: str, entity: EntityPlan, index: int, report: ValidationReport
    ) -> None:
        """Validate fields against the schema for this exact entity type."""
        schema = ENTITY_DIMENSION_SCHEMAS.get(kind)
        if schema is None:
            return

        for name, rule in schema.items():
            if rule.required and name not in entity.dimensions:
                report.errors.append(
                    ValidationIssue(
                        "dimension_missing",
                        f"{kind} requires dimension {name}",
                        index,
                    )
                )

        for name, value in entity.dimensions.items():
            rule = schema.get(name)
            if rule is None:
                report.errors.append(
                    ValidationIssue(
                        "dimension_field_unsupported",
                        f"Dimension {name} is not valid for {kind}",
                        index,
                    )
                )
                continue
            if rule.value_type == "boolean":
                if type(value) is not bool:
                    report.errors.append(
                        ValidationIssue(
                            "dimension_type_invalid",
                            f"Dimension {name} must be boolean",
                            index,
                        )
                    )
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                report.errors.append(
                    ValidationIssue(
                        "dimension_type_invalid",
                        f"Dimension {name} must be numeric",
                        index,
                    )
                )
                continue
            numeric = float(value)
            if not math.isfinite(numeric):
                report.errors.append(
                    ValidationIssue("dimension_invalid", f"Dimension {name} is not finite", index)
                )
            elif rule.value_type == "positive" and numeric <= 0:
                report.errors.append(
                    ValidationIssue(
                        "dimension_nonpositive",
                        f"Dimension {name} must be positive",
                        index,
                    )
                )
            elif rule.value_type == "angle" and not 0 <= numeric < 360:
                report.errors.append(
                    ValidationIssue(
                        "dimension_angle_invalid",
                        f"Dimension {name} must be in [0, 360)",
                        index,
                    )
                )

    def _validate_entity_geometry(
        self, kind: str, entity: EntityPlan, index: int, report: ValidationReport
    ) -> None:
        """Reject entity-specific degenerate geometry before AutoCAD sees it."""
        coordinates = entity.coordinates
        if kind == "line":
            self._reject_coincident_points(
                coordinates.get("start"),
                coordinates.get("end"),
                "line_zero_length",
                "Line start and end points must be distinct",
                index,
                report,
            )
        elif kind == "rectangle":
            corner1 = _point(coordinates.get("corner1"))
            corner2 = _point(coordinates.get("corner2"))
            if corner1 is not None and corner2 is not None:
                width = abs(corner2[0] - corner1[0])
                height = abs(corner2[1] - corner1[1])
                if width <= GEOMETRY_TOLERANCE:
                    report.errors.append(
                        ValidationIssue(
                            "rectangle_width_nonpositive",
                            "Rectangle width must be positive",
                            index,
                        )
                    )
                if height <= GEOMETRY_TOLERANCE:
                    report.errors.append(
                        ValidationIssue(
                            "rectangle_height_nonpositive",
                            "Rectangle height must be positive",
                            index,
                        )
                    )
                if width * height <= 0:
                    report.errors.append(
                        ValidationIssue(
                            "rectangle_area_nonpositive",
                            "Rectangle area must be positive",
                            index,
                        )
                    )
        elif kind == "polyline":
            self._validate_polyline(entity, index, report)
        elif kind == "arc":
            start = entity.dimensions.get("start_angle")
            end = entity.dimensions.get("end_angle")
            if (
                isinstance(start, bool)
                or isinstance(end, bool)
                or not isinstance(start, (int, float))
                or not isinstance(end, (int, float))
                or not math.isfinite(float(start))
                or not math.isfinite(float(end))
                or not 0 <= float(start) < 360
                or not 0 <= float(end) < 360
                or math.isclose(float(start), float(end), abs_tol=GEOMETRY_TOLERANCE)
            ):
                report.errors.append(
                    ValidationIssue(
                        "arc_sweep_invalid",
                        "Arc requires a finite, non-zero sweep with angles in [0, 360)",
                        index,
                    )
                )
        elif kind in {"aligned_dimension", "linear_dimension"}:
            self._reject_coincident_points(
                coordinates.get("start"),
                coordinates.get("end"),
                "dimension_defining_points_coincident",
                "Dimension start and end points must be distinct",
                index,
                report,
            )
        elif kind == "diametric_dimension":
            self._reject_coincident_points(
                coordinates.get("chord_point"),
                coordinates.get("far_chord_point"),
                "dimension_defining_points_coincident",
                "Diameter chord points must be distinct",
                index,
                report,
            )
        elif kind == "radial_dimension":
            self._reject_coincident_points(
                coordinates.get("center"),
                coordinates.get("chord_point"),
                "dimension_defining_points_coincident",
                "Radius center and chord point must be distinct",
                index,
                report,
            )

    @staticmethod
    def _reject_coincident_points(
        first: Any,
        second: Any,
        code: str,
        message: str,
        index: int,
        report: ValidationReport,
    ) -> None:
        point1 = _point(first)
        point2 = _point(second)
        if (
            point1 is not None
            and point2 is not None
            and _distance(point1, point2) <= GEOMETRY_TOLERANCE
        ):
            report.errors.append(ValidationIssue(code, message, index))

    @staticmethod
    def _validate_polyline(entity: EntityPlan, index: int, report: ValidationReport) -> None:
        raw_points = entity.coordinates.get("points")
        if not isinstance(raw_points, list):
            return
        points = [_point(value) for value in raw_points]
        if any(point is None for point in points):
            return
        valid_points = [point for point in points if point is not None]
        closed_value = entity.dimensions.get("closed", False)
        closed = closed_value if type(closed_value) is bool else False
        unique: list[tuple[float, float, float]] = []
        for point in valid_points:
            if not any(_distance(point, seen) <= GEOMETRY_TOLERANCE for seen in unique):
                unique.append(point)
        if len(unique) != len(valid_points):
            report.errors.append(
                ValidationIssue(
                    "polyline_repeated_vertex",
                    "Polyline vertices must not repeat",
                    index,
                )
            )
        minimum = 3 if closed else 2
        if len(unique) < minimum:
            report.errors.append(
                ValidationIssue(
                    "polyline_unique_vertices_invalid",
                    f"{'Closed' if closed else 'Open'} polyline requires at least "
                    f"{minimum} unique vertices",
                    index,
                )
            )
        segments = list(zip(valid_points, valid_points[1:]))
        if closed and len(valid_points) >= 2:
            segments.append((valid_points[-1], valid_points[0]))
        if any(_distance(start, end) <= GEOMETRY_TOLERANCE for start, end in segments):
            report.errors.append(
                ValidationIssue(
                    "polyline_zero_length_segment",
                    "Polyline must not contain zero-length segments",
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
                    and all(_distance(centers[0], center) <= tolerance for center in centers[1:])
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
                passed = len(distances) >= 2 and max(distances) - min(distances) <= tolerance
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
            report.checks.append(f"entity[{index}] constraint {constraint.kind}: passed")

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
            (math.degrees(math.atan2(p[1] - center[1], p[0] - center[0])) % 360) for p in points
        )
        gaps = [(angles[(i + 1) % len(angles)] - angles[i]) % 360 for i in range(len(angles))]
        return all(abs(gap - expected_angle) <= tolerance for gap in gaps)

    @staticmethod
    def _tangent(data: dict[str, Any], tolerance: float) -> bool:
        center1 = _point(data.get("center1"))
        center2 = _point(data.get("center2"))
        if center1 and center2:
            r1, r2 = float(data["radius1"]), float(data["radius2"])
            distance = _distance(center1, center2)
            return min(abs(distance - (r1 + r2)), abs(distance - abs(r1 - r2))) <= tolerance
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
