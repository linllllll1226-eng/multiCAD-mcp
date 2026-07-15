"""
Opt-in stdio-only multiCAD server with local memory and validation tools.

This entry point deliberately does not start the web dashboard or bind a port.
The existing ``server.py`` remains unchanged for backward compatibility.
"""

import logging
import os
import sys

os.environ.setdefault("MULTICAD_STRICT_GUARDED_WRITES", "1")

from mcp_tools.tools.memory import register_memory_tools
from mcp_tools.tools.tasks import register_task_tools
from mcp_tools.tools.validation import register_validation_tools
from server import mcp

logger = logging.getLogger(__name__)

register_memory_tools(mcp)
register_validation_tools(mcp)
register_task_tools(mcp)


if __name__ == "__main__":
    try:
        logger.info("Starting enhanced multiCAD-MCP in local stdio-only mode")
        mcp.run(transport="stdio")
    except KeyboardInterrupt:
        logger.info("Enhanced multiCAD-MCP stopped by user")
    except Exception as exc:
        logger.error("Enhanced multiCAD-MCP failed: %s", exc, exc_info=True)
        sys.exit(1)
