"""Normalize and parse common engineering drawing annotations."""

from __future__ import annotations

import re
from typing import Any

_NUMBER = r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)"


def normalize_engineering_text(text: str) -> str:
    """Return a conservative normalized representation of OCR/PDF text."""
    value = text.strip().upper()
    value = value.replace("Φ", "Ø").replace("⌀", "Ø")
    value = re.sub(r"\b(?:DIA|DIAMETER)\.?\s*", "Ø", value)
    value = value.replace("+/-", "±").replace("+-", "±")
    value = re.sub(r"\bDEG(?:REE)?S?\b", "°", value)
    value = re.sub(r"\b(\d+)\s*(?:PLACES?|PLCS?|X)\b", r"\1 PLCS", value)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"Ø\s+", "Ø", value)
    value = re.sub(r"R\s+(?=\d)", "R", value)
    return value.strip()


def _record(
    kind: str,
    normalized: str,
    value: float | str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "kind": kind,
        "normalized": normalized,
        "value": value,
        "confidence": 1.0,
    }
    record.update({key: item for key, item in extra.items() if item is not None})
    return record


def parse_dimension_text(text: str) -> list[dict[str, Any]]:
    """Parse one annotation into bounded, machine-readable dimension records."""
    normalized = normalize_engineering_text(text)
    if not normalized:
        return []

    thread = re.search(
        rf"\b(M\d+(?:\.\d+)?(?:\s*[X×]\s*{_NUMBER})?|"
        rf"\d+\s*/\s*\d+\s*-\s*\d+\s+(?:UNC|UNF)(?:-\d+[AB])?)\b",
        normalized,
    )
    if thread:
        return [_record("thread", normalized, thread.group(1).replace(" ", ""))]

    diameter = re.search(rf"Ø\s*({_NUMBER})", normalized)
    if diameter:
        return [_record("diameter", normalized, float(diameter.group(1)), unit="mm")]

    radius = re.search(rf"\bR\s*({_NUMBER})", normalized)
    if radius:
        return [_record("radius", normalized, float(radius.group(1)), unit="mm")]

    angle = re.search(rf"({_NUMBER})\s*°", normalized)
    if angle:
        return [_record("angle", normalized, float(angle.group(1)), unit="degree")]

    depth = re.search(rf"(?:DEPTH|DEEP|深)\s*({_NUMBER})", normalized)
    if depth:
        return [_record("depth", normalized, float(depth.group(1)), unit="mm")]

    count = re.search(r"\b(\d+)\s+PLCS\b", normalized)
    if count:
        return [_record("count", normalized, int(count.group(1)), unit="count")]

    tolerance = re.search(rf"({_NUMBER})\s*±\s*({_NUMBER})", normalized)
    if tolerance:
        return [
            _record(
                "linear",
                normalized,
                float(tolerance.group(1)),
                tolerance=float(tolerance.group(2)),
                unit="mm",
            )
        ]

    if re.fullmatch(rf"{_NUMBER}", normalized):
        return [_record("linear", normalized, float(normalized), unit="mm")]
    return []
