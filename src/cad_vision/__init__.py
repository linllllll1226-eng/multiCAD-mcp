"""Optional, read-only source analysis for CAD images and vector PDFs."""

from .analyzer import analyze_source, vision_capabilities
from .dimensions import normalize_engineering_text, parse_dimension_text

__all__ = [
    "analyze_source",
    "normalize_engineering_text",
    "parse_dimension_text",
    "vision_capabilities",
]
