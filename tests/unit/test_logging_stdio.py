"""Exercise startup logging through the actual guarded stdio entry point."""

import asyncio
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.integration
@pytest.mark.parametrize("blocked", [False, True], ids=["writable", "blocked"])
@pytest.mark.parametrize("acceptance_entry", [False, True], ids=["normal", "acceptance"])
def test_guarded_stdio_starts_with_optional_file_logging(
    tmp_path: Path, blocked: bool, acceptance_entry: bool
) -> None:
    """A failed file sink must not break initialize/tools-list or pollute stdout."""
    project = Path(__file__).resolve().parents[2]
    log_dir = tmp_path / "logs"
    if blocked:
        # Deterministic on Windows and in elevated CI: a file cannot be a directory.
        log_dir.write_text("existing file; do not overwrite", encoding="utf-8")
    stderr_path = tmp_path / "stderr.log"
    arguments = [str(project / "src" / "server_memory.py")]
    if acceptance_entry:
        arguments = [
            str(project / "scripts" / "Run-Isolated-Dwg-Acceptance.py"),
            "--evidence-dir",
            str(tmp_path / "evidence"),
        ]
    parameters = StdioServerParameters(
        command=sys.executable,
        args=arguments,
        cwd=str(project),
        env={
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "MULTICAD_LOG_DIR": str(log_dir),
            "MULTICAD_STRICT_GUARDED_WRITES": "1",
        },
    )

    async def handshake() -> list[str]:
        with stderr_path.open("w", encoding="utf-8") as stderr:
            async with stdio_client(parameters, errlog=stderr) as (read, write):
                async with ClientSession(
                    read, write, read_timeout_seconds=timedelta(seconds=20)
                ) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    return sorted(tool.name for tool in result.tools)

    async def bounded_handshake() -> list[str]:
        return await asyncio.wait_for(handshake(), timeout=30)

    names = asyncio.run(bounded_handshake())
    assert {"cad_plan_validate", "cad_execute_plan", "cad_verify_execution"}.issubset(names)
    assert ("cad_acceptance_document" in names) is acceptance_entry
    assert not (tmp_path / "evidence" / "test_document_state.json").exists()
    assert len(names) == len(set(names))
    stderr_text = stderr_path.read_text(encoding="utf-8")
    if blocked:
        assert "Continuing with stderr" in stderr_text
        assert log_dir.read_text(encoding="utf-8") == "existing file; do not overwrite"
    else:
        assert "Starting enhanced multiCAD-MCP" in (log_dir / "multicad_mcp.log").read_text(
            encoding="utf-8"
        )
        assert "File logging unavailable" not in stderr_text
    (tmp_path / "stdio-result.json").write_text(
        json.dumps(
            {
                "passed": True,
                "blocked_log_directory": blocked,
                "entrypoint": parameters.args[0],
                "tool_count": len(names),
                "tools": names,
                "cad_tool_calls": 0,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
