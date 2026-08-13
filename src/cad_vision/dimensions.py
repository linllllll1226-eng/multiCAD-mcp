"""Normalize and parse common engineering drawing annotations."""

from __future__ import annotations

import re
from typing import Any

_NUMBER = r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)"
_LENGTH_UNIT_ALIASES = {
    "MM": "mm",
    "MILLIMETER": "mm",
    "MILLIMETERS": "mm",
    "CM": "cm",
    "CENTIMETER": "cm",
    "CENTIMETERS": "cm",
    "M": "m",
    "METER": "m",
    "METERS": "m",
    "IN": "inch",
    "INCH": "inch",
    "INCHES": "inch",
    '"': "inch",
    "FT": "foot",
    "FOOT": "foot",
    "FEET": "foot",
    "'": "foot",
}


def normalize_length_unit(unit: str | None) -> str | None:
    """Normalize an explicit length unit without inventing a default."""
    if unit is None or not str(unit).strip():
        return None
    normalized = str(unit).strip().upper()
    if normalized not in _LENGTH_UNIT_ALIASES:
        raise ValueError(f"Unsupported dimension unit: {unit}")
    return _LENGTH_UNIT_ALIASES[normalized]


def normalize_engineering_text(text: str) -> str:
    """Return a conservative normalized representation of OCR/PDF text."""
    value = text.strip().upper()
    value = value.replace("Φ", "Ø").replace("⌀", "Ø")
    value = re.sub(r"\b(?:DIA|DIAMETER)\.?\s*", "Ø", value)
    value = value.replace("+/-", "±").replace("+-", "±")
    value = re.sub(r"\bDEG(?:REE)?S?\b", "°", value)
    value = value.replace("×", "X")
    value = re.sub(r"\b(\d+)\s*(?:PLACES?|PLCS?)\b", r"\1 PLCS", value)
    value = re.sub(
        r"^\s*(\d+)\s*X\s*(?=(?:Ø|R\s*\d|M\d|\d+\s*/))",
        r"\1 PLCS ",
        value,
    )
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
    record.update(extra)
    return record


def _explicit_unit(normalized: str) -> str | None:
    if re.search(rf"{_NUMBER}\s*\"", normalized):
        return "inch"
    annotated_unit = re.search(
        rf"{_NUMBER}\s*(MM|CM|M|INCHES|INCH|IN|FEET|FOOT|FT)\b",
        normalized,
    )
    if annotated_unit:
        return _LENGTH_UNIT_ALIASES[annotated_unit.group(1)]
    if re.search(rf"{_NUMBER}\s*'", normalized):
        return "foot"
    return None


def _length_metadata(
    normalized: str,
    *,
    default_unit: str | None,
    unit_source: str | None,
) -> dict[str, Any]:
    explicit = _explicit_unit(normalized)
    if explicit is not None:
        return {"unit": explicit, "unit_source": "annotation", "unit_resolved": True}
    resolved = normalize_length_unit(default_unit)
    return {
        "unit": resolved,
        "unit_source": unit_source if resolved is not None else (unit_source or "unresolved"),
        "unit_resolved": resolved is not None,
    }


def parse_dimension_text(
    text: str,
    *,
    default_unit: str | None = None,
    unit_source: str | None = None,
) -> list[dict[str, Any]]:
    """Parse one annotation into bounded, machine-readable dimension records."""
    normalized = normalize_engineering_text(text)
    if not normalized:
        return []

    length_metadata = _length_metadata(
        normalized,
        default_unit=default_unit,
        unit_source=unit_source,
    )
    records: list[dict[str, Any]] = []

    count = re.search(r"\b(\d+)\s+PLCS\b", normalized)
    if count:
        records.append(
            _record(
                "count",
                normalized,
                int(count.group(1)),
                unit="count",
                unit_source="annotation",
                unit_resolved=True,
            )
        )

    thread = re.search(
        rf"\b(M\d+(?:\.\d+)?(?:\s*X\s*{_NUMBER})?(?:-\d+[A-Z])?|"
        rf"\d+\s*/\s*\d+\s*-\s*\d+\s*(?:UNC|UNF|UNEF)(?:-\d+[AB])?)\b",
        normalized,
    )
    if thread:
        thread_value = thread.group(1).replace(" ", "")
        metric = thread_value.startswith("M")
        thread_unit = "mm" if metric else "inch"
        tolerance_class = re.search(r"-(\d+[A-Z])$", thread_value)
        records.append(
            _record(
                "thread",
                normalized,
                thread_value,
                unit=thread_unit,
                unit_source="thread_standard",
                unit_resolved=True,
                tolerance_class=tolerance_class.group(1) if tolerance_class else None,
            )
        )
        if not length_metadata["unit_resolved"]:
            length_metadata = {
                "unit": thread_unit,
                "unit_source": "thread_standard",
                "unit_resolved": True,
            }

    diameter = re.search(rf"Ø\s*({_NUMBER})", normalized)
    if diameter:
        records.append(_record("diameter", normalized, float(diameter.group(1)), **length_metadata))

    radius = re.search(rf"\bR\s*({_NUMBER})", normalized)
    if radius:
        records.append(_record("radius", normalized, float(radius.group(1)), **length_metadata))

    angle = re.search(rf"({_NUMBER})\s*°", normalized)
    if angle:
        records.append(
            _record(
                "angle",
                normalized,
                float(angle.group(1)),
                unit="degree",
                unit_source="annotation",
                unit_resolved=True,
            )
        )

    depth = re.search(rf"(?:DEPTH|DEEP|深|↧)\s*({_NUMBER})", normalized)
    depth_value = depth.group(1) if depth else None
    if depth_value is None:
        depth = re.search(rf"({_NUMBER})\s*(?:DEPTH|DEEP|深|↧)", normalized)
        depth_value = depth.group(1) if depth else None
    if depth_value is not None:
        records.append(_record("depth", normalized, float(depth_value), **length_metadata))

    # PaddleOCR commonly maps a diameter/depth callout such as "Ø20 ↧ 65"
    # to "20V65". Preserve it as a low-confidence candidate so the model asks
    # for confirmation instead of silently losing the hole callout or promoting
    # damaged OCR text to a formal dimension.
    symbolic_depth = re.search(rf"Ø?\s*({_NUMBER})\s*[V∨⌄⌵]\s*({_NUMBER})", normalized)
    if symbolic_depth and depth_value is None:
        first, second = float(symbolic_depth.group(1)), float(symbolic_depth.group(2))
        if not diameter:
            records.append(
                _record(
                    "diameter",
                    normalized,
                    first,
                    **length_metadata,
                    confidence=0.65,
                    needs_confirmation=True,
                    inference="damaged_diameter_depth_symbol",
                )
            )
        records.append(
            _record(
                "depth",
                normalized,
                second,
                **length_metadata,
                confidence=0.65 if not diameter else 0.85,
                needs_confirmation=True,
                inference="damaged_depth_symbol",
            )
        )

    tolerance = re.search(
        rf"({_NUMBER})\s*(?:MM|CM|M|IN|INCH|INCHES|FT|FOOT|FEET|\"|')?\s*"
        rf"±\s*({_NUMBER})",
        normalized,
    )
    if tolerance:
        base = float(tolerance.group(1))
        tolerance_value = abs(float(tolerance.group(2)))
        target = next(
            (
                record
                for record in records
                if record["kind"] in {"diameter", "radius", "depth"}
                and isinstance(record.get("value"), (int, float))
                and abs(float(record["value"]) - base) <= 1e-9
            ),
            None,
        )
        if target is not None:
            target["tolerance"] = tolerance_value
        else:
            records.append(
                _record(
                    "linear",
                    normalized,
                    base,
                    tolerance=tolerance_value,
                    **length_metadata,
                )
            )

    plain_number = re.fullmatch(
        rf"({_NUMBER})\s*(?:MM|CM|M|IN|INCH|INCHES|FT|FOOT|FEET|\"|')?",
        normalized,
    )
    if not records and plain_number:
        return [_record("linear", normalized, float(plain_number.group(1)), **length_metadata)]
    return records
