from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

SPEC = importlib.util.spec_from_file_location(
    "vision_tool_contract", Path(__file__).resolve().parents[2] / "src/mcp_tools/tools/vision.py"
)
vision_tools = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vision_tools)
register_vision_tools = vision_tools.register_vision_tools


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

    monkeypatch.setattr(vision_tools, "analyze_source", fake_analyze)
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
            ocr_rotation_angles=[90, 270],
        )
    )

    assert payload == {"ok": True}
    assert captured["ocr_policy"] == "force"
    assert captured["raster_page_threshold"] == 0.25
    assert captured["raster_region_threshold"] == 0.05
    assert captured["source_unit"] == "inch"
    assert captured["drawing_unit"] == "inch"
    assert captured["ocr_rotation_angles"] == [90, 270]
