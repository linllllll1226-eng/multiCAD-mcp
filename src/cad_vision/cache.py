"""Reuse successful analysis evidence without making cache I/O a prerequisite."""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject ambiguous duplicate keys in a persisted result."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate cache key: {key}")
        result[key] = value
    return result


def _finite_float(value: str) -> float:
    """Reject named nonfinite values and exponent overflow anywhere in JSON."""
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"Nonfinite cache value: {value}")
    return parsed


def _reusable(result: dict[str, Any]) -> bool:
    """Keep transient OCR failures out of persistent result reuse."""
    analysis = result.get("analysis")
    if not isinstance(analysis, dict):
        return False
    ocr = analysis.get("ocr")
    if not isinstance(ocr, dict) or not isinstance(ocr.get("dimensions"), list):
        return False
    policy = analysis.get("ocr_policy")
    status = ocr.get("status")
    return (
        (policy == "off" and status == "disabled")
        or (policy == "auto" and status == "not_required")
        or (policy in ("auto", "force") and status == "ok")
    )


def load_result(
    path: Path, *, source: dict[str, Any], options: dict[str, Any]
) -> dict[str, Any] | None:
    """Return matching evidence, treating unreadable or invalid cache as a miss."""
    try:
        cached = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_pairs,
            parse_float=_finite_float,
            parse_constant=_finite_float,
        )
    except FileNotFoundError:
        return None
    except (OSError, ValueError, RecursionError) as exc:
        logger.warning("Ignoring unreadable vision cache %s: %s", path, exc)
        return None

    if not isinstance(cached, dict) or not _reusable(cached):
        return None
    cached_source = cached.get("source")
    if not isinstance(cached_source, dict) or any(
        cached_source.get(key) != value for key, value in source.items() if key != "name"
    ):
        return None
    if any(cached.get(key) != options[key] for key in ("pipeline_version", "runtime_fingerprint")):
        return None
    analysis = cached["analysis"]
    if analysis.get("ocr_policy") != options["ocr_policy"]:
        return None
    resolution = analysis.get("unit_resolution")
    if not isinstance(resolution, dict) or any(
        resolution.get(key) != options[option]
        for key, option in (
            ("unit", "resolved_unit"),
            ("source_unit", "source_unit"),
            ("drawing_unit", "drawing_unit"),
            ("status", "unit_resolution_status"),
        )
    ):
        return None
    if not isinstance(analysis.get("dimensions"), list) or analysis.get("dimension_count") != len(
        analysis["dimensions"]
    ):
        return None
    # Equal bytes/options share evidence, but each request reports its current filename.
    cached["source"] = dict(source)
    return cached


def save_result(path: Path, result: dict[str, Any]) -> None:
    """Publish complete JSON atomically; cache failures never discard fresh analysis."""
    if not _reusable(result):
        return
    temporary: Path | None = None
    try:
        payload = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.stem}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        os.replace(temporary, path)
    except (OSError, ValueError) as exc:
        logger.warning("Could not persist vision cache %s: %s", path, exc)
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
