"""Optional, read-only source analysis for CAD images and vector PDFs."""

from .analyzer import analyze_source, vision_capabilities
from .audit_renderer import (
    audit_primitives,
    compare_expected_manifest,
    normalize_entities,
    render_task_audit,
)
from .dimensions import normalize_engineering_text, parse_dimension_text
from .ocr import clear_pipeline_cache, extract_ocr, ocr_capabilities

__all__ = [
    "analyze_source",
    "audit_primitives",
    "compare_expected_manifest",
    "clear_pipeline_cache",
    "extract_ocr",
    "normalize_engineering_text",
    "normalize_entities",
    "ocr_capabilities",
    "parse_dimension_text",
    "render_task_audit",
    "vision_capabilities",
]
