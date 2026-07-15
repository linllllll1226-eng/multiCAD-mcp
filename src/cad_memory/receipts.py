"""Short-lived receipts that bind validation to one exact CAD plan."""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

from .models import DrawingPlan

UNIT_CODE_TO_NAME = {
    0: "unitless",
    1: "in",
    2: "ft",
    3: "mi",
    4: "mm",
    5: "cm",
    6: "m",
    7: "km",
    8: "microin",
    9: "mil",
    10: "yd",
    11: "angstrom",
    12: "nm",
    13: "micron",
    14: "dm",
    15: "dam",
    16: "hm",
    17: "gm",
    18: "au",
    19: "ly",
    20: "pc",
}

UNIT_ALIASES = {
    "millimeter": "mm",
    "millimeters": "mm",
    "millimetre": "mm",
    "millimetres": "mm",
    "毫米": "mm",
    "centimeter": "cm",
    "centimeters": "cm",
    "厘米": "cm",
    "meter": "m",
    "meters": "m",
    "metre": "m",
    "metres": "m",
    "米": "m",
    "inch": "in",
    "inches": "in",
    "英寸": "in",
    "foot": "ft",
    "feet": "ft",
    "英尺": "ft",
    "unitless": "unitless",
    "none": "unitless",
}

SUPPORTED_PLAN_UNITS = frozenset(UNIT_CODE_TO_NAME.values())


def normalize_unit(value: str | None) -> str | None:
    """Return a stable unit name for comparison."""
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not normalized:
        return None
    return UNIT_ALIASES.get(normalized, normalized)


def read_document_unit(document: Any) -> dict[str, Any]:
    """Read AutoCAD INSUNITS without modifying the active drawing."""
    try:
        code = int(document.GetVariable("INSUNITS"))
    except Exception:
        return {"code": None, "name": None, "readable": False}
    return {
        "code": code,
        "name": UNIT_CODE_TO_NAME.get(code, f"insunits:{code}"),
        "readable": True,
    }


def canonical_plan_hash(plan: DrawingPlan | dict[str, Any]) -> str:
    """Hash a canonical JSON representation of the complete plan."""
    if isinstance(plan, DrawingPlan):
        payload = plan.model_dump(mode="json")
    else:
        payload = DrawingPlan.model_validate(plan).model_dump(mode="json")
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _identity_key(identity: dict[str, Any]) -> tuple[str, str]:
    return (
        str(identity.get("drawing_name") or "").casefold(),
        str(identity.get("drawing_full_name") or "").casefold(),
    )


@dataclass
class ValidationReceipt:
    """One one-time validation result tied to plan, drawing, and units."""

    validation_id: str
    plan_hash: str
    drawing_identity: dict[str, Any]
    drawing_unit_code: int | None
    created_monotonic: float
    expires_monotonic: float
    consumed: bool = False

    def public_dict(self) -> dict[str, Any]:
        """Return the safe fields required by the next workflow step."""
        return {
            "validation_id": self.validation_id,
            "plan_hash": self.plan_hash,
            "drawing_identity": self.drawing_identity,
            "drawing_unit_code": self.drawing_unit_code,
            "expires_in_seconds": max(
                0, int(self.expires_monotonic - time.monotonic())
            ),
            "one_time": True,
        }


class ValidationReceiptStore:
    """Keep one-time receipts in memory so server restarts fail closed."""

    def __init__(self, ttl_seconds: int = 600) -> None:
        """Create an in-memory store with a bounded receipt lifetime."""
        self.ttl_seconds = max(30, int(ttl_seconds))
        self._receipts: dict[str, ValidationReceipt] = {}
        self._lock = threading.RLock()

    def issue(
        self,
        plan: DrawingPlan,
        drawing_identity: dict[str, Any],
        drawing_unit_code: int | None,
    ) -> ValidationReceipt:
        """Create a short-lived receipt for a plan that already passed validation."""
        now = time.monotonic()
        receipt = ValidationReceipt(
            validation_id=secrets.token_urlsafe(24),
            plan_hash=canonical_plan_hash(plan),
            drawing_identity=dict(drawing_identity),
            drawing_unit_code=drawing_unit_code,
            created_monotonic=now,
            expires_monotonic=now + self.ttl_seconds,
        )
        with self._lock:
            self._purge_expired(now)
            self._receipts[receipt.validation_id] = receipt
        return receipt

    def consume(
        self,
        validation_id: str,
        plan: DrawingPlan,
        drawing_identity: dict[str, Any],
        drawing_unit_code: int | None,
    ) -> ValidationReceipt:
        """Consume a receipt only when all bound state is unchanged."""
        if not validation_id:
            raise PermissionError(
                "cad_execute_plan requires validation_id from cad_plan_validate"
            )
        now = time.monotonic()
        with self._lock:
            receipt = self._receipts.get(validation_id)
            if receipt is None:
                raise PermissionError("Validation receipt is missing or expired")
            if receipt.consumed:
                raise PermissionError("Validation receipt has already been consumed")
            if receipt.expires_monotonic <= now:
                self._receipts.pop(validation_id, None)
                raise PermissionError("Validation receipt has expired")
            if receipt.plan_hash != canonical_plan_hash(plan):
                raise PermissionError(
                    "Plan changed after validation; validate the revised plan again"
                )
            if _identity_key(receipt.drawing_identity) != _identity_key(
                drawing_identity
            ):
                raise PermissionError(
                    "Active drawing changed after validation; validate again"
                )
            if receipt.drawing_unit_code != drawing_unit_code:
                raise PermissionError(
                    "Drawing INSUNITS changed after validation; validate again"
                )
            receipt.consumed = True
            return receipt

    def _purge_expired(self, now: float) -> None:
        expired = [
            key
            for key, receipt in self._receipts.items()
            if receipt.expires_monotonic <= now
        ]
        for key in expired:
            self._receipts.pop(key, None)


VALIDATION_RECEIPTS = ValidationReceiptStore()
