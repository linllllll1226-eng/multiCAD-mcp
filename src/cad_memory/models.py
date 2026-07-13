"""Structured models for safe CAD drawing plans."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

DimensionSource = Literal[
    "explicit_dimension",
    "geometric_constraint",
    "user_confirmed",
    "approximate_reference",
]
OperationType = Literal["create", "modify", "delete", "layout_only"]


class ConstraintSpec(BaseModel):
    """A checkable geometric relationship."""

    kind: Literal[
        "symmetry",
        "concentric",
        "tangent",
        "equal_distance",
        "uniform_distribution",
        "dimension_chain",
    ]
    data: dict[str, Any] = Field(default_factory=dict)
    tolerance: float = Field(default=1e-6, gt=0)


class EntityPlan(BaseModel):
    """One planned CAD entity or controlled edit."""

    entity_type: str = Field(min_length=1)
    coordinates: dict[str, Any] = Field(default_factory=dict)
    dimensions: dict[str, float] = Field(default_factory=dict)
    layer: str = Field(min_length=1)
    linetype: str = "ByLayer"
    dimension_source: DimensionSource
    confidence: float = Field(ge=0.0, le=1.0)
    constraints: list[ConstraintSpec] = Field(default_factory=list)
    uncertain_items: list[str] = Field(default_factory=list)
    operation: OperationType = "create"
    target_handles: list[str] = Field(default_factory=list)
    text_override: str = ""
    background_fill: bool = False


class DrawingPlan(BaseModel):
    """Complete, user-reviewable plan for one CAD write task."""

    task_name: str = Field(min_length=1)
    unit: str | None = None
    entities: list[EntityPlan] = Field(min_length=1)
    existing_layers: list[str] = Field(default_factory=list)
    uncertain_items: list[str] = Field(default_factory=list)
    user_confirmed: bool = False
    allow_delete: bool = False
    allow_overwrite: bool = False
    preview_mode: bool = True
    tolerance: float = Field(default=1e-6, gt=0)
