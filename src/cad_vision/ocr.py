"""Optional local OCR integration for scanned engineering drawings."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

from .dimensions import parse_dimension_text

_PIPELINE_LOCK = threading.RLock()
_PIPELINES: dict[tuple[str, str], Any] = {}
DEFAULT_MODEL_CACHE = Path(__file__).resolve().parents[2] / "data" / "paddle_models"
_LANGUAGE_ALIASES = {
    "eng": "en",
    "english": "en",
    "zh": "ch",
    "zh-cn": "ch",
    "zh_cn": "ch",
}


def _normalize_language(language: str) -> str:
    normalized = language.strip().lower()
    if not normalized:
        raise ValueError("language is required")
    return _LANGUAGE_ALIASES.get(normalized, normalized)


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
    from paddleocr import PaddleOCR  # type: ignore[import-untyped]

    return PaddleOCR(
        lang=_normalize_language(language),
        ocr_version="PP-OCRv5",
        device=device,
        engine="paddle_static",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )


def _get_pipeline(language: str, device: str) -> Any:
    language = _normalize_language(language)
    key = (language, device)
    with _PIPELINE_LOCK:
        if key not in _PIPELINES:
            _PIPELINES[key] = _create_pipeline(language, device)
        return _PIPELINES[key]


def clear_pipeline_cache() -> None:
    """Release cached OCR pipeline objects, primarily for tests and upgrades."""
    with _PIPELINE_LOCK:
        _PIPELINES.clear()


def _discard_pipeline(language: str, device: str) -> None:
    """Discard one possibly-corrupted native OCR pipeline after a runtime error."""
    key = (_normalize_language(language), device)
    with _PIPELINE_LOCK:
        _PIPELINES.pop(key, None)


def _predict_with_retry(
    pipeline: Any,
    source: Path,
    *,
    language: str,
    device: str,
    min_confidence: float,
    pipeline_factory: Callable[[str, str], Any] | None,
) -> tuple[Any, bool]:
    """Run PaddleOCR once, rebuilding its native pipeline for one safe retry."""
    try:
        return pipeline.predict(str(source), text_rec_score_thresh=min_confidence), False
    except Exception:
        if pipeline_factory is not None:
            raise
        _discard_pipeline(language, device)
        fresh_pipeline = _get_pipeline(language, device)
        try:
            result = fresh_pipeline.predict(str(source), text_rec_score_thresh=min_confidence)
        except Exception:
            # A failed rebuild is not safe to reuse on the next request.
            _discard_pipeline(language, device)
            raise
        return result, True


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


def _mapped_box(
    value: Any,
    *,
    scale: float = 1.0,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
    page_transform: tuple[float, float, float, float, float, float] | None = None,
) -> list[float] | None:
    box = _box(value)
    if box is None:
        return None
    x0 = box[0] / scale + offset_x
    y0 = box[1] / scale + offset_y
    x1 = box[2] / scale + offset_x
    y1 = box[3] / scale + offset_y
    if page_transform is None:
        return [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)]

    a, b, c, d, e, f = page_transform
    points = [
        (a * x + c * y + e, b * x + d * y + f) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    ]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return [
        round(min(xs), 3),
        round(min(ys), 3),
        round(max(xs), 3),
        round(max(ys), 3),
    ]


def _pdf_ocr_inputs(
    source: Path,
    temp_root: Path,
    *,
    page_numbers: list[int],
    regions: dict[int, list[list[float]]],
    max_pages: int,
    scale: float,
) -> list[dict[str, Any]]:
    """Render canonical PDF regions and retain their device-to-page transforms."""
    try:
        import fitz  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - dependency-specific
        raise RuntimeError("Selected-page PDF OCR requires PyMuPDF") from exc

    inputs: list[dict[str, Any]] = []
    with fitz.open(source) as document:
        limit = min(len(document), max(1, min(int(max_pages), 50)))
        full_pages = set(page_numbers)
        selected = sorted(full_pages | set(regions))
        for page_number in selected:
            if page_number < 1 or page_number > limit:
                raise ValueError(f"OCR page {page_number} is outside the analyzed PDF range")
            page = document[page_number - 1]
            canonical_page_rect = page.rect * page.derotation_matrix
            clips: list[Any]
            if page_number in full_pages:
                clips = [page.rect]
            else:
                clips = []
                for raw in regions.get(page_number, []):
                    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
                        raise ValueError("OCR regions must contain four page coordinates")
                    canonical_clip = (
                        fitz.Rect(*(float(value) for value in raw)) & canonical_page_rect
                    )
                    if canonical_clip.is_empty:
                        continue
                    # get_pixmap() clips in the displayed / rotated Page.rect
                    # coordinate system, unlike text and vector extraction.
                    render_clip = (canonical_clip * page.rotation_matrix) & page.rect
                    if not render_clip.is_empty:
                        clips.append(render_clip)
            derotation = page.derotation_matrix
            page_transform = (
                float(derotation.a),
                float(derotation.b),
                float(derotation.c),
                float(derotation.d),
                float(derotation.e),
                float(derotation.f),
            )
            for region_index, clip in enumerate(clips):
                pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
                target = temp_root / f"page-{page_number}-region-{region_index}.png"
                pixmap.save(target)
                inputs.append(
                    {
                        "path": target,
                        "page": page_number,
                        "scale": scale,
                        # Pixmap origins include device-pixel rounding.  Using
                        # them instead of clip.x0/y0 prevents sub-pixel drift.
                        "offset_x": float(pixmap.x) / scale,
                        "offset_y": float(pixmap.y) / scale,
                        "page_transform": page_transform,
                    }
                )
    return inputs


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
    page_numbers: list[int] | None = None,
    regions: dict[int, list[list[float]]] | None = None,
    default_unit: str | None = None,
    unit_source: str | None = None,
    pdf_render_scale: float = 2.0,
) -> dict[str, Any]:
    """Run local OCR and return text boxes plus parsed dimension candidates."""
    min_confidence = float(min_confidence)
    if not 0.0 <= min_confidence <= 1.0:
        raise ValueError("min_confidence must be between 0 and 1")
    language = _normalize_language(language)

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
        page_numbers = sorted(set(page_numbers or [])) if page_numbers is not None else None
        regions = regions or {}
        pdf_selection = source.suffix.lower() == ".pdf" and (
            page_numbers is not None or bool(regions)
        )
        temporary_directory: tempfile.TemporaryDirectory[str] | None = None
        pages_by_number: dict[int, list[dict[str, Any]]] = {}
        texts: list[dict[str, Any]] = []
        dimensions: list[dict[str, Any]] = []
        pipeline_rebuilt = False
        try:
            if pdf_selection:
                if pdf_render_scale <= 0:
                    raise ValueError("pdf_render_scale must be positive")
                temporary_directory = tempfile.TemporaryDirectory(prefix="multicad-ocr-")
                inputs = _pdf_ocr_inputs(
                    source,
                    Path(temporary_directory.name),
                    page_numbers=page_numbers or [],
                    regions=regions,
                    max_pages=max_pages,
                    scale=float(pdf_render_scale),
                )
            else:
                inputs = [
                    {
                        "path": source,
                        "page": None,
                        "scale": 1.0,
                        "offset_x": 0.0,
                        "offset_y": 0.0,
                        "page_transform": None,
                    }
                ]

            for input_spec in inputs:
                raw_results, rebuilt = _predict_with_retry(
                    pipeline,
                    input_spec["path"],
                    language=language,
                    device=device,
                    min_confidence=min_confidence,
                    pipeline_factory=pipeline_factory,
                )
                pipeline_rebuilt = pipeline_rebuilt or rebuilt
                if rebuilt and pipeline_factory is None:
                    pipeline = _get_pipeline(language, device)
                for fallback_page, raw_result in enumerate(raw_results):
                    if fallback_page >= max(1, min(int(max_pages), 50)):
                        break
                    payload = _json_result(raw_result)
                    rec_texts = list(payload.get("rec_texts") or [])
                    rec_scores = list(payload.get("rec_scores") or [])
                    rec_boxes = list(payload.get("rec_boxes") or payload.get("rec_polys") or [])
                    page_number = input_spec["page"] or (
                        int(payload.get("page_index", fallback_page) or 0) + 1
                    )
                    page_items = pages_by_number.setdefault(page_number, [])
                    for index, text in enumerate(rec_texts):
                        confidence = float(rec_scores[index]) if index < len(rec_scores) else 0.0
                        if confidence < min_confidence or not str(text).strip():
                            continue
                        bbox = _mapped_box(
                            rec_boxes[index] if index < len(rec_boxes) else None,
                            scale=float(input_spec["scale"]),
                            offset_x=float(input_spec["offset_x"]),
                            offset_y=float(input_spec["offset_y"]),
                            page_transform=input_spec.get("page_transform"),
                        )
                        item: dict[str, Any] = {
                            "text": str(text).strip(),
                            "confidence": round(confidence, 4),
                            "bbox": bbox,
                            "page": page_number,
                            "provider": "paddleocr",
                        }
                        page_items.append(item)
                        texts.append(item)
                        for parsed in parse_dimension_text(
                            item["text"],
                            default_unit=default_unit,
                            unit_source=unit_source,
                        ):
                            parse_confidence = float(parsed.get("confidence", 1.0))
                            combined_confidence = round(parse_confidence * item["confidence"], 4)
                            parsed.update(
                                {
                                    "page": page_number,
                                    "source_text": item["text"],
                                    "confidence": combined_confidence,
                                    "ocr_confidence": item["confidence"],
                                    "parse_confidence": parse_confidence,
                                    "bbox": bbox,
                                    "provenance": [
                                        {
                                            "provider": "paddleocr",
                                            "source": "ocr",
                                            "page": page_number,
                                            "bbox": bbox,
                                            "confidence": item["confidence"],
                                        }
                                    ],
                                }
                            )
                            dimensions.append(parsed)
        finally:
            if temporary_directory is not None:
                temporary_directory.cleanup()

        pages = [
            {
                "page": page_number,
                "text_count": len(page_items),
                "text_samples": page_items[:sample_limit] if include_samples else [],
            }
            for page_number, page_items in sorted(pages_by_number.items())
        ]
        result: dict[str, Any] = {
            "status": "ok",
            "provider": "paddleocr",
            "language": language,
            "device": device,
            "minimum_confidence": min_confidence,
            "page_count_analyzed": len(pages),
            "pipeline_rebuilt_after_error": pipeline_rebuilt,
            "text_count": len(texts),
            "dimension_count": len(dimensions),
            "dimensions": dimensions[:200],
            "pages": pages,
            "selection": {
                "page_numbers": page_numbers,
                "regions": regions,
                "pdf_render_scale": pdf_render_scale if pdf_selection else None,
            },
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
