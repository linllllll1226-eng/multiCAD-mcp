"""Tests for validation receipts, plan hashes, and real drawing units."""

import pytest

from cad_memory.models import DrawingPlan
from cad_memory.receipts import (
    ValidationReceiptStore,
    canonical_plan_hash,
    read_document_unit,
)
from cad_memory.validator import PlanValidator


def _plan(*, radius: float = 5, unit: str = "mm") -> DrawingPlan:
    return DrawingPlan.model_validate(
        {
            "task_name": "receipt-test",
            "unit": unit,
            "user_confirmed": True,
            "existing_layers": ["AI_PREVIEW_OUTLINE"],
            "entities": [
                {
                    "entity_type": "circle",
                    "coordinates": {"center": [0, 0, 0]},
                    "dimensions": {"radius": radius},
                    "layer": "AI_PREVIEW_OUTLINE",
                    "linetype": "ByLayer",
                    "dimension_source": "explicit_dimension",
                    "confidence": 1,
                }
            ],
        }
    )


def _identity(name: str = "Drawing1.dwg") -> dict[str, str]:
    return {"drawing_name": name, "drawing_full_name": name}


class FakeDocument:
    """Minimal document exposing AutoCAD's INSUNITS variable."""

    def __init__(self, unit_code: int) -> None:
        """Store the INSUNITS code returned by the fake document."""
        self.unit_code = unit_code

    def GetVariable(self, name: str):  # noqa: N802 - AutoCAD COM name
        assert name == "INSUNITS"
        return self.unit_code


def test_plan_hash_is_canonical_and_changes_with_geometry():
    first = _plan()
    same = DrawingPlan.model_validate(first.model_dump(mode="json"))
    changed = _plan(radius=6)
    assert canonical_plan_hash(first) == canonical_plan_hash(same)
    assert canonical_plan_hash(first) != canonical_plan_hash(changed)


def test_receipt_binds_plan_drawing_and_unit_and_is_one_time():
    store = ValidationReceiptStore(ttl_seconds=60)
    plan = _plan()
    receipt = store.issue(plan, _identity(), 4)
    consumed = store.consume(receipt.validation_id, plan, _identity(), 4)
    assert consumed.plan_hash == canonical_plan_hash(plan)
    with pytest.raises(PermissionError, match="already been consumed"):
        store.consume(receipt.validation_id, plan, _identity(), 4)


def test_receipt_rejects_changed_plan_drawing_and_unit():
    store = ValidationReceiptStore(ttl_seconds=60)
    plan = _plan()
    changed_plan = store.issue(plan, _identity(), 4)
    with pytest.raises(PermissionError, match="Plan changed"):
        store.consume(changed_plan.validation_id, _plan(radius=6), _identity(), 4)

    changed_drawing = store.issue(plan, _identity(), 4)
    with pytest.raises(PermissionError, match="Active drawing changed"):
        store.consume(
            changed_drawing.validation_id,
            plan,
            _identity("Drawing2.dwg"),
            4,
        )

    changed_unit = store.issue(plan, _identity(), 4)
    with pytest.raises(PermissionError, match="INSUNITS changed"):
        store.consume(changed_unit.validation_id, plan, _identity(), 1)


def test_document_unit_reads_autocad_insunits():
    assert read_document_unit(FakeDocument(4)) == {
        "code": 4,
        "name": "mm",
        "readable": True,
    }


def test_validator_blocks_unit_mismatch_and_warns_for_unitless():
    plan = _plan()
    mismatch = PlanValidator().validate(
        plan,
        available_layers=["AI_PREVIEW_OUTLINE"],
        drawing_unit="in",
    )
    assert not mismatch.passed
    assert any(issue.code == "drawing_unit_mismatch" for issue in mismatch.errors)

    unitless = PlanValidator().validate(
        plan,
        available_layers=["AI_PREVIEW_OUTLINE"],
        drawing_unit="unitless",
    )
    assert unitless.passed
    assert any(
        issue.code == "drawing_unit_unitless" for issue in unitless.warnings
    )
