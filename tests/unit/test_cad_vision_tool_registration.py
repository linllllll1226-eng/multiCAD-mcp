from __future__ import annotations

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


def test_registers_two_read_only_tools() -> None:
    mcp = FakeMCP()
    register_vision_tools(mcp)
    assert set(mcp.tools) == {"cad_vision_capabilities", "cad_analyze_source"}
