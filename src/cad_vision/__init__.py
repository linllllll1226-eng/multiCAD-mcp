"""Optional, read-only source analysis for CAD images and vector PDFs."""

from .analyzer import analyze_source, vision_capabilities
from .dimensions import normalize_engineering_text, parse_dimension_text
from .ocr import clear_pipeline_cache, extract_ocr, ocr_capabilities

__all__ = [
    "analyze_source",
    "clear_pipeline_cache",
    "extract_ocr",
    "normalize_engineering_text",
    "ocr_capabilities",
    "parse_dimension_text",
    "vision_capabilities",
]
