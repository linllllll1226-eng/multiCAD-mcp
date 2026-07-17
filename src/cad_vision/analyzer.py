"""Safe source routing, capability reporting, and local result caching."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from .image import analyze_image_geometry
from .ocr import extract_ocr, ocr_capabilities
from .pdf import extract_vector_pdf

PIPELINE_VERSION = "1.3.1"
SUPPORTED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "vision_cache"

_SAMPLE_KEYS = {
    "line_samples",
    "circle_samples",
    "close_parallel_pairs",
    "vector_samples",
    "text_samples",
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
            "PaddleOCR is used locally only when requested and installed.",
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


def analyze_source(
    source_path: str,
    max_pages: int = 10,
    use_cache: bool = True,
    include_samples: bool = True,
    use_ocr: bool = False,
    ocr_language: str = "ch",
    ocr_min_confidence: float = 0.5,
) -> dict[str, Any]:
    """Analyze a local CAD source and return a compact structured result."""
    started = time.perf_counter()
    source = _validated_source(source_path)
    max_pages = max(1, min(int(max_pages), 50))
    digest = _digest(source)
    options = {
        "pipeline_version": PIPELINE_VERSION,
        "max_pages": max_pages,
        "sample_policy": "canonical_bounded",
        "use_ocr": bool(use_ocr),
        "ocr_language": ocr_language,
        "ocr_min_confidence": float(ocr_min_confidence),
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
        )
    else:
        analysis = analyze_image_geometry(source, include_samples=True)

    should_run_ocr = use_ocr and (
        source.suffix.lower() != ".pdf" or analysis.get("text_word_count", 0) == 0
    )
    if should_run_ocr:
        analysis["ocr"] = extract_ocr(
            source,
            language=ocr_language,
            min_confidence=ocr_min_confidence,
            max_pages=max_pages,
            include_samples=True,
        )
    elif use_ocr:
        analysis["ocr"] = {
            "status": "skipped_vector_text",
            "provider": "vector_pdf",
            "text_count": analysis.get("text_word_count", 0),
            "dimension_count": len(analysis.get("dimensions", [])),
            "dimensions": analysis.get("dimensions", []),
            "reason": "Embedded vector text was available, so raster OCR was unnecessary.",
        }

    result: dict[str, Any] = {
        "source": {
            "name": source.name,
            "suffix": source.suffix.lower(),
            "size_bytes": source.stat().st_size,
            "sha256": digest,
        },
        "pipeline_version": PIPELINE_VERSION,
        "cache_hit": False,
        "analysis": analysis,
        "request_ms": round((time.perf_counter() - started) * 1000.0, 3),
    }
    if use_cache:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return _result_view(result, include_samples=include_samples)
