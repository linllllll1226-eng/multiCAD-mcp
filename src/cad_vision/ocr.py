"""Optional local OCR integration for scanned engineering drawings."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import threading
from pathlib import Path
from typing import Any, Callable

from .dimensions import parse_dimension_text

_PIPELINE_LOCK = threading.RLock()
_PIPELINES: dict[tuple[str, str], Any] = {}
DEFAULT_MODEL_CACHE = Path(__file__).resolve().parents[2] / "data" / "paddle_models"


def _configure_runtime_paths() -> Path:
    """Keep Paddle model files on an ASCII-safe, user-overridable local path."""
    configured = Path(
        os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(DEFAULT_MODEL_CACHE))
    ).expanduser()
    configured.mkdir(parents=True, exist_ok=True)
    return configured


def ocr_capabilities() -> dict[str, Any]:
    """Return local PaddleOCR and inference-engine availability."""
    paddleocr_installed = importlib.util.find_spec("paddleocr") is not None
    paddle_installed = importlib.util.find_spec("paddle") is not None
    versions: dict[str, str] = {}
    for distribution in ("paddleocr", "paddlepaddle"):
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            continue
    return {
        "provider": "paddleocr",
        "available": paddleocr_installed and paddle_installed,
        "paddleocr_installed": paddleocr_installed,
        "paddle_engine_installed": paddle_installed,
        "versions": versions,
        "local_inference": True,
        "model_download_may_be_required": True,
        "model_cache": str(
            Path(os.environ.get("PADDLE_PDX_CACHE_HOME", str(DEFAULT_MODEL_CACHE))).expanduser()
        ),
        "supported_languages": ["ch", "chinese_cht", "en"],
    }


def _create_pipeline(language: str, device: str) -> Any:
    _configure_runtime_paths()
    from paddleocr import PaddleOCR

    return PaddleOCR(
        lang=language,
        device=device,
        engine="paddle_static",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )


def _get_pipeline(language: str, device: str) -> Any:
    key = (language, device)
    with _PIPELINE_LOCK:
        if key not in _PIPELINES:
            _PIPELINES[key] = _create_pipeline(language, device)
        return _PIPELINES[key]


def clear_pipeline_cache() -> None:
    """Release cached OCR pipeline objects, primarily for tests and upgrades."""
    with _PIPELINE_LOCK:
        _PIPELINES.clear()


def _json_result(result: Any) -> dict[str, Any]:
    payload = getattr(result, "json", result)
    if callable(payload):
        payload = payload()
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise TypeError("PaddleOCR result must expose a JSON object")
    nested = payload.get("res")
    return nested if isinstance(nested, dict) else payload


def _box(value: Any) -> list[float] | None:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, (list, tuple)) or not value:
        return None
    if len(value) == 4 and all(isinstance(item, (int, float)) for item in value):
        return [round(float(item), 2) for item in value]
    points = [
        item
        for item in value
        if isinstance(item, (list, tuple))
        and len(item) >= 2
        and all(isinstance(number, (int, float)) for number in item[:2])
    ]
    if not points:
        return None
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return [round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)]


def extract_ocr(
    source: Path,
    *,
    language: str = "ch",
    device: str = "cpu",
    min_confidence: float = 0.5,
    max_pages: int = 10,
    include_samples: bool = True,
    sample_limit: int = 100,
    pipeline_factory: Callable[[str, str], Any] | None = None,
) -> dict[str, Any]:
    """Run local OCR and return text boxes plus parsed dimension candidates."""
    min_confidence = float(min_confidence)
    if not 0.0 <= min_confidence <= 1.0:
        raise ValueError("min_confidence must be between 0 and 1")
    if not language.strip():
        raise ValueError("language is required")

    capabilities = ocr_capabilities()
    if pipeline_factory is None and not capabilities["available"]:
        return {
            "status": "unavailable",
            "provider": "paddleocr",
            "language": language,
            "text_count": 0,
            "dimension_count": 0,
            "dimensions": [],
            "reason": "Install the optional 'ocr' dependencies to enable local OCR.",
        }

    try:
        pipeline = (
            pipeline_factory(language, device)
            if pipeline_factory is not None
            else _get_pipeline(language, device)
        )
        raw_results = pipeline.predict(
            str(source),
            text_rec_score_thresh=min_confidence,
        )
        pages: list[dict[str, Any]] = []
        texts: list[dict[str, Any]] = []
        dimensions: list[dict[str, Any]] = []
        for fallback_page, raw_result in enumerate(raw_results):
            if fallback_page >= max(1, min(int(max_pages), 50)):
                break
            payload = _json_result(raw_result)
            rec_texts = list(payload.get("rec_texts") or [])
            rec_scores = list(payload.get("rec_scores") or [])
            rec_boxes = list(payload.get("rec_boxes") or payload.get("rec_polys") or [])
            page_number = int(payload.get("page_index", fallback_page) or 0) + 1
            page_items: list[dict[str, Any]] = []
            for index, text in enumerate(rec_texts):
                confidence = float(rec_scores[index]) if index < len(rec_scores) else 0.0
                if confidence < min_confidence or not str(text).strip():
                    continue
                item = {
                    "text": str(text).strip(),
                    "confidence": round(confidence, 4),
                    "bbox": _box(rec_boxes[index]) if index < len(rec_boxes) else None,
                    "page": page_number,
                }
                page_items.append(item)
                texts.append(item)
                for parsed in parse_dimension_text(item["text"]):
                    parsed.update(
                        {
                            "page": page_number,
                            "source_text": item["text"],
                            "confidence": item["confidence"],
                            "bbox": item["bbox"],
                        }
                    )
                    dimensions.append(parsed)
            pages.append(
                {
                    "page": page_number,
                    "text_count": len(page_items),
                    "text_samples": page_items[:sample_limit] if include_samples else [],
                }
            )
        result: dict[str, Any] = {
            "status": "ok",
            "provider": "paddleocr",
            "language": language,
            "device": device,
            "minimum_confidence": min_confidence,
            "page_count_analyzed": len(pages),
            "text_count": len(texts),
            "dimension_count": len(dimensions),
            "dimensions": dimensions[:200],
            "pages": pages,
        }
        if include_samples:
            result["text_samples"] = texts[:sample_limit]
        return result
    except Exception as exc:  # dependency and model-runtime errors are reported safely
        return {
            "status": "error",
            "provider": "paddleocr",
            "language": language,
            "text_count": 0,
            "dimension_count": 0,
            "dimensions": [],
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
