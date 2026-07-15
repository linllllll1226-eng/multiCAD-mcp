"""Read-only MCP tools for CAD source analysis."""

from __future__ import annotations

import json
from typing import Any

from cad_vision import analyze_source, vision_capabilities


def _result(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def register_vision_tools(mcp: Any) -> None:
    """Register source-analysis tools that never connect to or modify CAD."""

    @mcp.tool()
    def cad_vision_capabilities() -> str:
        """Report installed vector-PDF, raster-geometry, and OCR capabilities."""
        return _result(vision_capabilities())

    @mcp.tool()
    def cad_analyze_source(
        source_path: str,
        max_pages: int = 10,
        use_cache: bool = True,
        include_samples: bool = True,
        use_ocr: bool = True,
        ocr_language: str = "ch",
        ocr_min_confidence: float = 0.5,
    ) -> str:
        """Analyze one local PDF/image without reading or writing an AutoCAD DWG."""
        return _result(
            analyze_source(
                source_path,
                max_pages=max_pages,
                use_cache=use_cache,
                include_samples=include_samples,
                use_ocr=use_ocr,
                ocr_language=ocr_language,
                ocr_min_confidence=ocr_min_confidence,
            )
        )
