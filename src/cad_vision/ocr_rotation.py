"""Opt-in orthogonal OCR probes with source-coordinate and disagreement evidence."""

from __future__ import annotations

import math
import unicodedata
from copy import deepcopy
from pathlib import Path
from typing import Any


def normalized_rotations(values: list[int] | None) -> list[int]:
    """Validate bounded counterclockwise probes; the original pass is implicit."""
    if values is None:
        return []
    if not isinstance(values, list) or any(
        type(value) is not int or value not in (90, 180, 270) for value in values
    ):
        raise ValueError("ocr_rotation_angles must be a list containing only 90, 180, 270")
    return sorted(set(values))


def rotated_inputs(inputs: list[dict], angles: list[int], root: Path) -> list[dict]:
    """Render lossless right-angle probes without changing original source files."""
    from PIL import Image

    variants = []
    operations = {
        90: Image.Transpose.ROTATE_90,
        180: Image.Transpose.ROTATE_180,
        270: Image.Transpose.ROTATE_270,
    }
    for index, original in enumerate(inputs):
        variants.append({**original, "rotation_degrees": 0})
        with Image.open(original["path"]) as image:
            if getattr(image, "n_frames", 1) != 1:
                raise ValueError(
                    "rotation probes require single-frame images or rendered PDF pages"
                )
            for angle in angles:
                target = root / f"probe-{index}-{angle}.png"
                image.convert("RGB").transpose(operations[angle]).save(target)
                variants.append(
                    {
                        **original,
                        "path": target,
                        "rotation_degrees": angle,
                        "original_size": image.size,
                    }
                )
    return variants


def unrotated_box(box: list[float] | None, spec: dict) -> list[float] | None:
    """Map a probe's pixel box back before the existing PDF crop/page transform."""
    angle = spec.get("rotation_degrees", 0)
    if box is None or not angle:
        return box
    width, height = spec["original_size"]
    x0, y0, x1, y1 = box
    if angle == 90:
        return [width - y1, x0, width - y0, x1]
    if angle == 180:
        return [width - x1, height - y1, width - x0, height - y0]
    return [y0, height - x1, y1, height - x0]


def _same_location(first: dict, second: dict) -> bool:
    """Require same-page, substantially overlapping boxes of comparable area."""
    if first.get("page") != second.get("page"):
        return False
    a, b = first.get("bbox"), second.get("bbox")
    if a is None or b is None:
        return False
    area_a, area_b = (a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])
    if not all(math.isfinite(value) and value > 0 for value in (area_a, area_b)):
        return False
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1])
    )
    return (
        max(area_a, area_b) <= 4 * min(area_a, area_b)
        and intersection >= 0.5 * min(area_a, area_b)
        and intersection / (area_a + area_b - intersection) >= 0.2
    )


def merge_orientation_evidence(texts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate agreeing probes and retain conflicts as unconfirmed evidence."""
    merged: list[dict[str, Any]] = []
    for raw in texts:
        item = deepcopy(raw)
        rotation = item.get("rotation_degrees", 0)
        observation = {
            key: item.get(key) for key in ("text", "confidence", "bbox", "rotation_degrees")
        }
        normalized = unicodedata.normalize("NFKC", item["text"]).strip()
        # Repeated values on separate lines, or two detections in a single
        # pass, must not collapse into one observation.
        duplicate = next(
            (
                other
                for other in merged
                if rotation not in other["orientation_degrees"]
                and unicodedata.normalize("NFKC", other["text"]).strip() == normalized
                and _same_location(other, item)
            ),
            None,
        )
        if duplicate is not None:
            duplicate["orientation_degrees"].append(rotation)
            duplicate["orientation_observations"].append(observation)
            duplicate["confidence"] = max(duplicate["confidence"], item["confidence"])
        else:
            item["orientation_degrees"] = [rotation]
            item["orientation_observations"] = [observation]
            merged.append(item)
    for item in merged:
        conflicts = [
            other["text"]
            for other in merged
            if other is not item and other["text"] != item["text"] and _same_location(item, other)
        ]
        reasons = list(item.get("confirmation_reasons", []))
        if 0 not in item["orientation_degrees"]:
            reasons.append("rotation_only_candidate")
        if conflicts:
            reasons.append("orientation_disagreement")
        item["needs_confirmation"] = bool(reasons)
        item["confirmation_reasons"] = reasons
        item["orientation_conflicts"] = sorted(set(conflicts))
    return merged
