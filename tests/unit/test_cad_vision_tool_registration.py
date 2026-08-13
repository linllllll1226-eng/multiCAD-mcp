from __future__ import annotations

import json
from typing import Any

from mcp_tools.tools.vision import register_vision_tools


class FakeMCP:
    """Minimal decorator-compatible MCP registry used by the unit test."""

    def __init__(self) -> None:
        """Initialize an empty tool registry."""
        self.tools: dict[str, Any] = {}

    def tool(self) -> Any:
        def decorator(function: Any) -> Any:
            self.tools[function.__name__] = function
            return function

        return decorator


def test_registers_three_read_only_tools() -> None:
    mcp = FakeMCP()
    register_vision_tools(mcp)
    assert set(mcp.tools) == {
        "cad_vision_capabilities",
        "cad_analyze_source",
        "cad_capture_live_window",
    }


def test_analysis_tool_forwards_hybrid_policy_and_unit_options(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_analyze(source_path: str, **kwargs: Any) -> dict[str, Any]:
        captured["source_path"] = source_path
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr("mcp_tools.tools.vision.analyze_source", fake_analyze)
    mcp = FakeMCP()
    register_vision_tools(mcp)
    payload = json.loads(
        mcp.tools["cad_analyze_source"](
            "drawing.pdf",
            ocr_policy="force",
            raster_page_threshold=0.25,
            raster_region_threshold=0.05,
            source_unit="inch",
            drawing_unit="inch",
        )
    )

    assert payload == {"ok": True}
    assert captured["ocr_policy"] == "force"
    assert captured["raster_page_threshold"] == 0.25
    assert captured["raster_region_threshold"] == 0.05
    assert captured["source_unit"] == "inch"
    assert captured["drawing_unit"] == "inch"
