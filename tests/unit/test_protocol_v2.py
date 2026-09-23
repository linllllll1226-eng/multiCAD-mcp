"""Exercise both protocol eras and profile sync over real isolated STDIO."""

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.integration
@pytest.mark.parametrize("modern", [False, True], ids=["initialize", "discover"])
def test_protocol_eras_call_results_and_errors(tmp_path, modern):
    async def exercise():
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(ROOT / "src/server_memory.py")],
            cwd=str(tmp_path),
            env={
                **os.environ,
                "MULTICAD_LOG_DIR": str(tmp_path / "logs"),
                "MULTICAD_DATA_DIR": str(tmp_path / "data"),
            },
        )
        with (tmp_path / "stderr.log").open("w", encoding="utf-8") as stderr:
            async with stdio_client(params, errlog=stderr) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=20) as client:
                    if modern:
                        await client.discover()
                        assert client.protocol_version == "2026-07-28"
                    else:
                        await client.initialize()
                        assert client.protocol_version == "2025-11-25"
                    tools = (await client.list_tools()).tools
                    assert len(tools) == 25
                    assert len({tool.name for tool in tools}) == 25
                    capability = next(t for t in tools if t.name == "cad_vision_capabilities")
                    assert capability.input_schema["type"] == "object"
                    result = await client.call_tool("cad_vision_capabilities", {})
                    assert not result.is_error
                    text = "".join(getattr(part, "text", "") for part in result.content)
                    payload = json.loads(text)
                    assert isinstance(payload, dict) and payload
                    assert result.structured_content["result"] == text
                    error = await client.call_tool("nonexistent_tool_for_error_test", {})
                    assert error.is_error
                    assert error.content
                    (tmp_path / "protocol-result.json").write_text(
                        json.dumps(
                            {
                                "protocol": client.protocol_version,
                                "tools": len(tools),
                                "cad_writes": 0,
                            }
                        ),
                        encoding="utf-8",
                    )

    asyncio.run(asyncio.wait_for(exercise(), timeout=40))


@pytest.mark.integration
def test_profile_sync_round_trip_uses_isolated_database(tmp_path, monkeypatch):
    from cad_runtime import resource_path
    from cad_ux.profiles import load_profiles

    spec = importlib.util.spec_from_file_location(
        "profile_sync", ROOT / "scripts/Sync-CAD-Profiles.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("MULTICAD_CAD_MEMORY_DB", str(tmp_path / "profiles.db"))
    monkeypatch.setenv("MULTICAD_LOG_DIR", str(tmp_path / "logs"))
    rows = asyncio.run(
        asyncio.wait_for(
            module.sync_profiles(
                load_profiles(resource_path("profiles")),
                sys.executable,
                str(ROOT / "src/server_memory.py"),
            ),
            timeout=40,
        )
    )
    assert len(rows) == 5
    assert all(row["round_trip_passed"] for row in rows)
    assert (tmp_path / "profiles.db").is_file()
