"""Safe source routing, capability reporting, and local result caching."""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from .dimensions import normalize_length_unit
from .image import analyze_image_geometry
from .ocr import extract_ocr, ocr_capabilities
from .pdf import extract_vector_pdf

PIPELINE_VERSION = "1.4.0"
SUPPORTED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
OCR_POLICIES = {"off", "auto", "force"}
OCR_RUNTIME_PROFILE = {
    "provider": "paddleocr",
    "ocr_version": "PP-OCRv5",
    "engine": "paddle_static",
    "device": "cpu",
}
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "vision_cache"

_SAMPLE_KEYS = {
    "line_samples",
    "circle_samples",
    "close_parallel_pairs",
    "vector_samples",
    "text_samples",
    "image_samples",
}


def _result_view(result: dict[str, Any], *, include_samples: bool) -> dict[str, Any]:
    """Return one request view without changing the canonical cached payload."""
    view = deepcopy(result)
    if include_samples:
        view["samples_included"] = True
        return view

    def strip(value: Any) -> None:
        if isinstance(value, dict):
            for key in tuple(value):
                if key in _SAMPLE_KEYS:
                    value.pop(key, None)
                else:
                    strip(value[key])
        elif isinstance(value, list):
            for item in value:
                strip(item)

    strip(view)
    view["samples_included"] = False
    return view


def vision_capabilities() -> dict[str, Any]:
    """Report optional dependency availability without importing heavy packages."""
    packages = {
        "pymupdf": importlib.util.find_spec("fitz") is not None,
        "opencv": importlib.util.find_spec("cv2") is not None,
        "numpy": importlib.util.find_spec("numpy") is not None,
    }
    ocr = ocr_capabilities()
    return {
        "pipeline_version": PIPELINE_VERSION,
        "packages": packages,
        "vector_pdf_available": packages["pymupdf"],
        "raster_geometry_available": packages["opencv"] and packages["numpy"],
        "ocr_provider_available": ocr["available"],
        "ocr": ocr,
        "supported_extensions": sorted(SUPPORTED_SUFFIXES),
        "notes": [
            "Vector PDF extraction is preferred over raster OCR when available.",
            "Hybrid PDFs are checked page by page for raster regions that need OCR.",
            "PaddleOCR is used locally only when policy requests it and it is installed.",
            "The first OCR run may download official model weights.",
            "Analysis never writes to AutoCAD or bypasses the guarded CAD workflow.",
        ],
    }


def _validated_source(source_path: str) -> Path:
    if not source_path.strip():
        raise ValueError("source_path is required")
    if source_path.startswith("\\\\"):
        raise ValueError("UNC/network sources are not allowed")
    source = Path(source_path).expanduser().resolve(strict=True)
    if not source.is_file():
        raise ValueError("source_path must identify a local file")
    if source.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(f"Unsupported source extension: {source.suffix}")
    max_bytes = int(os.environ.get("MULTICAD_VISION_MAX_BYTES", 100 * 1024 * 1024))
    if source.stat().st_size > max_bytes:
        raise ValueError(f"Source exceeds MULTICAD_VISION_MAX_BYTES ({max_bytes})")

    configured_roots = os.environ.get("MULTICAD_VISION_INPUT_ROOTS", "").strip()
    if configured_roots:
        roots = [Path(item).expanduser().resolve() for item in configured_roots.split(";") if item]
        if not any(source == root or root in source.parents for root in roots):
            raise ValueError("source_path is outside MULTICAD_VISION_INPUT_ROOTS")
    return source


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _safe_distribution_version(distribution: str) -> str | None:
    """Return installed package metadata without importing optional runtimes."""
    try:
        return importlib.metadata.version(distribution)
    except (importlib.metadata.PackageNotFoundError, ValueError, OSError):
        return None


def _runtime_fingerprint(*, suffix: str, ocr_policy: str) -> dict[str, Any]:
    """Describe result-affecting local providers for deterministic cache isolation."""
    if suffix == ".pdf":
        source_provider: dict[str, Any] = {
            "provider": "pymupdf",
            "version": _safe_distribution_version("PyMuPDF"),
        }
    else:
        source_provider = {
            "provider": "opencv_numpy",
            "opencv_version": _safe_distribution_version("opencv-python-headless"),
            "numpy_version": _safe_distribution_version("numpy"),
        }

    fingerprint: dict[str, Any] = {"source": source_provider}
    if ocr_policy != "off":
        fingerprint["ocr"] = {
            **OCR_RUNTIME_PROFILE,
            "provider_version": _safe_distribution_version("paddleocr"),
            "runtime": "paddlepaddle",
            "runtime_version": _safe_distribution_version("paddlepaddle"),
        }
    return fingerprint


def _normalized_ocr_policy(*, use_ocr: bool, ocr_policy: str | None) -> str:
    policy = (ocr_policy or "").strip().lower()
    if not policy:
        return "auto" if use_ocr else "off"
    if policy not in OCR_POLICIES:
        raise ValueError(f"ocr_policy must be one of: {', '.join(sorted(OCR_POLICIES))}")
    return policy


def _unit_resolution(source_unit: str | None, drawing_unit: str | None) -> dict[str, Any]:
    source = normalize_length_unit(source_unit)
    drawing = normalize_length_unit(drawing_unit)
    if source is not None and drawing is not None and source != drawing:
        return {
            "status": "conflict",
            "unit": None,
            "unit_source": "source_drawing_conflict",
            "source_unit": source,
            "drawing_unit": drawing,
        }
    if source is not None and drawing is not None:
        return {
            "status": "resolved",
            "unit": source,
            "unit_source": "source_and_drawing_profile",
            "source_unit": source,
            "drawing_unit": drawing,
        }
    if source is not None:
        return {
            "status": "resolved",
            "unit": source,
            "unit_source": "source",
            "source_unit": source,
            "drawing_unit": None,
        }
    if drawing is not None:
        return {
            "status": "resolved",
            "unit": drawing,
            "unit_source": "drawing_profile",
            "source_unit": None,
            "drawing_unit": drawing,
        }
    return {
        "status": "unresolved",
        "unit": None,
        "unit_source": "unresolved",
        "source_unit": None,
        "drawing_unit": None,
    }


def _same_scalar(first: Any, second: Any) -> bool:
    if isinstance(first, (int, float)) and isinstance(second, (int, float)):
        return abs(float(first) - float(second)) <= 1e-9
    return first == second


def _normalized_box(value: Any) -> tuple[float, float, float, float] | None:
    if not (isinstance(value, (list, tuple)) and len(value) == 4):
        return None
    try:
        left, top, right, bottom = (float(item) for item in value)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in (left, top, right, bottom)):
        return None
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _location_similarity(first: Any, second: Any) -> float | None:
    """Return a bounded match score for similarly sized, substantially overlapping boxes."""
    first_box = _normalized_box(first)
    second_box = _normalized_box(second)
    if first_box is None or second_box is None:
        return None

    left = max(first_box[0], second_box[0])
    top = max(first_box[1], second_box[1])
    right = min(first_box[2], second_box[2])
    bottom = min(first_box[3], second_box[3])
    if right <= left or bottom <= top:
        return None

    first_area = (first_box[2] - first_box[0]) * (first_box[3] - first_box[1])
    second_area = (second_box[2] - second_box[0]) * (second_box[3] - second_box[1])
    smaller_area = min(first_area, second_area)
    larger_area = max(first_area, second_area)
    if larger_area / smaller_area > 4.0:
        return None

    intersection = (right - left) * (bottom - top)
    overlap_of_smaller = intersection / smaller_area
    union = first_area + second_area - intersection
    intersection_over_union = intersection / union
    if overlap_of_smaller < 0.5 or intersection_over_union < 0.2:
        return None
    return intersection_over_union


def _dimension_match_score(first: dict[str, Any], second: dict[str, Any]) -> float | None:
    if not (
        first.get("kind") == second.get("kind")
        and _same_scalar(first.get("value"), second.get("value"))
        and _same_scalar(first.get("tolerance"), second.get("tolerance"))
        and first.get("unit") == second.get("unit")
        and first.get("page") == second.get("page")
    ):
        return None
    return _location_similarity(first.get("bbox"), second.get("bbox"))


def _fallback_provenance(record: dict[str, Any], provider: str, source: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "source": source,
        "page": record.get("page"),
        "bbox": record.get("bbox"),
    }


def _merge_dimension_evidence(
    vector_dimensions: list[dict[str, Any]],
    ocr_dimensions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge only co-located equivalent evidence while preserving provenance."""
    merged: list[dict[str, Any]] = []
    candidates = [
        *((raw, "vector_text", "pymupdf") for raw in vector_dimensions),
        *((raw, "ocr", "paddleocr") for raw in ocr_dimensions),
    ]
    for raw, evidence_source, provider in candidates:
        candidate = deepcopy(raw)
        if not isinstance(candidate.get("provenance"), list) or not candidate["provenance"]:
            candidate["provenance"] = [_fallback_provenance(candidate, provider, evidence_source)]
        candidate["evidence_sources"] = [evidence_source]

        matches = [
            (score, item)
            for item in merged
            if (score := _dimension_match_score(item, candidate)) is not None
        ]
        if not matches:
            merged.append(candidate)
            continue
        _, existing = max(matches, key=lambda match: match[0])
        known = {
            json.dumps(item, ensure_ascii=False, sort_keys=True)
            for item in existing.get("provenance", [])
        }
        for item in candidate.get("provenance", []):
            serialized = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if serialized not in known:
                existing.setdefault("provenance", []).append(item)
                known.add(serialized)
        existing["evidence_sources"] = sorted(
            set(existing.get("evidence_sources", [])) | {evidence_source}
        )
        existing["confidence"] = max(
            float(existing.get("confidence", 0.0)),
            float(candidate.get("confidence", 0.0)),
        )
    return merged[:200]


def analyze_source(
    source_path: str,
    max_pages: int = 10,
    use_cache: bool = True,
    include_samples: bool = True,
    use_ocr: bool = False,
    ocr_language: str = "ch",
    ocr_min_confidence: float = 0.5,
    ocr_policy: str | None = None,
    raster_page_threshold: float = 0.15,
    raster_region_threshold: float = 0.02,
    source_unit: str | None = None,
    drawing_unit: str | None = None,
) -> dict[str, Any]:
    """Analyze a local CAD source and return a compact structured result."""
    started = time.perf_counter()
    source = _validated_source(source_path)
    max_pages = max(1, min(int(max_pages), 50))
    policy = _normalized_ocr_policy(use_ocr=use_ocr, ocr_policy=ocr_policy)
    raster_page_threshold = float(raster_page_threshold)
    raster_region_threshold = float(raster_region_threshold)
    if not 0.0 <= raster_page_threshold <= 1.0:
        raise ValueError("raster_page_threshold must be between 0 and 1")
    if not 0.0 <= raster_region_threshold <= 1.0:
        raise ValueError("raster_region_threshold must be between 0 and 1")
    unit_resolution = _unit_resolution(source_unit, drawing_unit)
    digest = _digest(source)
    runtime_fingerprint = _runtime_fingerprint(suffix=source.suffix.lower(), ocr_policy=policy)
    options = {
        "pipeline_version": PIPELINE_VERSION,
        "max_pages": max_pages,
        "sample_policy": "canonical_bounded",
        "ocr_policy": policy,
        "ocr_language": ocr_language,
        "ocr_min_confidence": float(ocr_min_confidence),
        "raster_page_threshold": raster_page_threshold,
        "raster_region_threshold": raster_region_threshold,
        "source_unit": unit_resolution["source_unit"],
        "drawing_unit": unit_resolution["drawing_unit"],
        "resolved_unit": unit_resolution["unit"],
        "unit_resolution_status": unit_resolution["status"],
        "runtime_fingerprint": runtime_fingerprint,
    }
    cache_key = hashlib.sha256(
        json.dumps({"sha256": digest, **options}, sort_keys=True).encode("utf-8")
    ).hexdigest()
    cache_dir = Path(os.environ.get("MULTICAD_VISION_CACHE", str(DEFAULT_CACHE_DIR)))
    cache_path = cache_dir / f"{cache_key}.json"

    if use_cache and cache_path.is_file():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        view = _result_view(cached, include_samples=include_samples)
        view["cache_hit"] = True
        view["request_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
        return view

    if source.suffix.lower() == ".pdf":
        analysis = extract_vector_pdf(
            source,
            max_pages=max_pages,
            include_samples=True,
            raster_page_threshold=raster_page_threshold,
            raster_region_threshold=raster_region_threshold,
            default_unit=unit_resolution["unit"],
            unit_source=unit_resolution["unit_source"],
        )
    else:
        analysis = analyze_image_geometry(source, include_samples=True)

    is_pdf = source.suffix.lower() == ".pdf"
    required_targets = (
        deepcopy(list(analysis.get("ocr_targets", [])))
        if is_pdf
        else [{"mode": "source", "reason": "raster_source"}]
    )
    full_pages: list[int] = []
    regions: dict[int, list[list[float]]] = {}
    selected_targets: list[dict[str, Any]] = []
    if policy == "force" and is_pdf:
        full_pages = list(range(1, int(analysis.get("page_count_analyzed", 0)) + 1))
        selected_targets = [
            {"page": page, "mode": "page", "regions": [], "reason": "forced"} for page in full_pages
        ]
    elif policy == "auto" and is_pdf:
        selected_targets = list(analysis.get("ocr_targets", []))
        full_pages = [
            int(target["page"]) for target in selected_targets if target.get("mode") == "page"
        ]
        regions = {
            int(target["page"]): list(target.get("regions", []))
            for target in selected_targets
            if target.get("mode") == "regions"
        }
    elif policy != "off":
        selected_targets = deepcopy(required_targets)

    should_run_ocr = policy != "off" and (not is_pdf or bool(selected_targets))
    if should_run_ocr:
        analysis["ocr"] = extract_ocr(
            source,
            language=ocr_language,
            min_confidence=ocr_min_confidence,
            max_pages=max_pages,
            include_samples=True,
            page_numbers=full_pages if is_pdf else None,
            regions=regions if is_pdf else None,
            default_unit=unit_resolution["unit"],
            unit_source=unit_resolution["unit_source"],
        )
    elif policy == "off":
        analysis["ocr"] = {
            "status": "disabled",
            "provider": "paddleocr",
            "dimensions": [],
            "reason": "OCR policy is off.",
        }
    else:
        analysis["ocr"] = {
            "status": "not_required",
            "provider": "paddleocr",
            "dimensions": [],
            "reason": "Page-level vector and raster coverage did not require OCR.",
        }

    vector_dimensions = list(analysis.get("dimensions", []))
    ocr_dimensions = list(analysis.get("ocr", {}).get("dimensions", []))
    analysis["vector_dimensions"] = vector_dimensions
    analysis["dimensions"] = _merge_dimension_evidence(vector_dimensions, ocr_dimensions)
    analysis["dimension_count"] = len(analysis["dimensions"])
    analysis["ocr_policy"] = policy
    analysis["ocr_targets_selected"] = selected_targets
    analysis["ocr_targets_required"] = required_targets
    analysis["unit_resolution"] = unit_resolution
    ocr_status = str(analysis.get("ocr", {}).get("status", "not_run"))
    source_requires_ocr = bool(required_targets)
    coverage_complete = not source_requires_ocr or (should_run_ocr and ocr_status == "ok")
    analysis["ocr_coverage"] = {
        "complete": coverage_complete,
        "required": source_requires_ocr,
        "status": ocr_status,
        "execution_requested": should_run_ocr,
        "gaps": [] if coverage_complete else deepcopy(required_targets),
    }

    result: dict[str, Any] = {
        "source": {
            "name": source.name,
            "suffix": source.suffix.lower(),
            "size_bytes": source.stat().st_size,
            "sha256": digest,
        },
        "pipeline_version": PIPELINE_VERSION,
        "runtime_fingerprint": runtime_fingerprint,
        "cache_hit": False,
        "analysis": analysis,
        "request_ms": round((time.perf_counter() - started) * 1000.0, 3),
    }
    if use_cache:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return _result_view(result, include_samples=include_samples)
