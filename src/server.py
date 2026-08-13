"""
multiCAD-MCP Server.

Fast, extensible MCP server for controlling multiple CAD applications.

Uses FastMCP framework for clean, decorator-based tool definition.
Supports AutoCAD, ZWCAD, GstarCAD, and other COM-compatible CAD software.
"""

import logging
import sys
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastmcp import FastMCP

from __version__ import __title__, __version__
from core import get_config, get_supported_cads
from mcp_tools.helpers import setup_logging, setup_utf8_encoding
from mcp_tools.tools import (
    register_block_tools,
    register_drawing_tools,
    register_entity_tools,
    register_export_tools,
    register_file_tools,
    register_layer_tools,
    register_session_tools,
)
from web.api import api_app, log_handler, shutdown_dashboard_worker

# Setup at module load
setup_utf8_encoding()
logger = setup_logging()
logging.getLogger().addHandler(log_handler)

# Initialize FastMCP server
mcp = FastMCP(name=__title__)


def register_all_tools():
    """Register all MCP tools with FastMCP.

    Organizes tools by category:
    - Session management (connection, view, history)
    - Drawing operations
    - Layer management
    - File operations
    - Entity selection and manipulation
    - Block management
    - Export and data extraction
    """
    logger.info("Registering MCP tools...")

    register_session_tools(mcp)
    logger.debug("  ✓ Session tools registered")

    register_drawing_tools(mcp)
    logger.debug("  ✓ Drawing tools registered")

    register_layer_tools(mcp)
    logger.debug("  ✓ Layer tools registered")

    register_file_tools(mcp)
    logger.debug("  ✓ File tools registered")

    register_entity_tools(mcp)
    logger.debug("  ✓ Entity tools registered")

    register_block_tools(mcp)
    logger.debug("  ✓ Block tools registered")

    register_export_tools(mcp)
    logger.debug("  ✓ Export tools registered")

    logger.info("All MCP tools registered successfully")


# Register tools at module load
register_all_tools()

# CAD connection is lazy-loaded on first tool use
logger.info(f"Starting multiCAD-MCP server v{__version__}...")
logger.info(f"Supported CAD types: {', '.join(get_supported_cads())}")
logger.info("CAD applications will be connected on first tool use (lazy loading)")


def build_http_app() -> Any:
    """Build the combined MCP/dashboard ASGI app with both lifespans active."""
    app = mcp.http_app()
    mcp_lifespan = app.router.lifespan_context
    dashboard_lifespan = api_app.router.lifespan_context

    @asynccontextmanager
    async def combined_lifespan(parent_app: Any) -> AsyncIterator[Any]:
        async with mcp_lifespan(parent_app) as mcp_state:
            async with dashboard_lifespan(api_app):
                yield mcp_state

    app.router.lifespan_context = combined_lifespan
    app.mount("/", api_app)
    return app


if __name__ == "__main__":
    import threading

    import uvicorn

    # Configuration from config.json
    config = get_config()
    host = config.dashboard.host
    port = config.dashboard.port

    # Autodetect transport mode:
    # - If stdin is a TTY (interactive terminal) → HTTP with dashboard
    # - If stdin is a pipe (Claude Desktop, etc.) → stdio
    use_stdio = not sys.stdin.isatty()
    dashboard_server = None
    web_thread = None

    def run_dashboard():
        """Helper to run uvicorn in a thread."""
        logger.info(f"Starting dashboard on http://{host}:{port}")
        if dashboard_server is not None:
            dashboard_server.run()

    try:
        if use_stdio:
            logger.info("Starting multiCAD-MCP server in stdio mode...")

            # Start dashboard in background
            dashboard_server = uvicorn.Server(
                uvicorn.Config(api_app, host=host, port=port, log_level="warning")
            )
            web_thread = threading.Thread(target=run_dashboard, daemon=True)
            web_thread.start()

            # Run MCP stdio
            mcp.run(transport="stdio")
        else:
            logger.info("Starting multiCAD-MCP server in HTTP mode...")

            # Mount dashboard while explicitly composing both ASGI lifespans.
            app = build_http_app()

            logger.info(f"Access dashboard at http://{host}:{port}/")
            logger.info(f"MCP endpoint at http://{host}:{port}/mcp")

            uvicorn.run(app, host=host, port=port)

    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Server error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        if dashboard_server is not None:
            dashboard_server.should_exit = True
        if web_thread is not None and web_thread.is_alive():
            web_thread.join(timeout=35.0)
        if web_thread is not None and web_thread.is_alive():
            logger.error("Dashboard HTTP thread did not stop before its shutdown deadline")
        if not shutdown_dashboard_worker():
            logger.error("Dashboard CAD worker did not stop before its shutdown deadline")
