"""Opt-in guarded MCP server for a fresh test DWG's save/close/reopen lifecycle."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path


def main() -> None:
    """Add the acceptance tool only when this explicit test entry point is launched."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--progid", default="AutoCAD.Application.24.1")
    args = parser.parse_args()
    project = args.project.resolve()
    if not (project / "src" / "server_memory.py").is_file():
        parser.error("--project must identify a multiCAD checkout")
    root = args.evidence_dir.resolve()
    sys.path.insert(0, str(project / "src"))
    os.environ.setdefault("MULTICAD_LOG_DIR", str(root / "logs"))
    os.environ["MULTICAD_STRICT_GUARDED_WRITES"] = "1"
    from adapters.com_gate import cad_operation
    from cad_memory.acceptance import execute_lifecycle
    from server_memory import mcp

    @mcp.tool()
    def cad_acceptance_document(action: str) -> str:
        """Prepare/snapshot/save_close/reopen/reclose only this run's fresh test DWG."""
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        try:
            with cad_operation(timeout=30):
                app = win32com.client.GetActiveObject(args.progid)
                return json.dumps(execute_lifecycle(action, app, root), ensure_ascii=False)
        finally:
            pythoncom.CoUninitialize()

    logging.getLogger(__name__).info("Starting enhanced multiCAD-MCP (isolated DWG acceptance)")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
