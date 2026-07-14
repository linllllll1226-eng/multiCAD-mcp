"""Validate profiles and optionally sync them through existing MCP profile tools."""

# ruff: noqa: E402, I001

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cad_ux.profiles import load_profiles, memory_tool_arguments  # noqa: E402


def _text(result: Any) -> str:
    return "".join(getattr(item, "text", "") for item in result.content)


async def _call_json(session: Any, name: str, arguments: dict[str, Any]) -> Any:
    result = await session.call_tool(name, arguments)
    text = _text(result)
    if getattr(result, "isError", False):
        raise RuntimeError(f"{name} failed: {text}")
    return json.loads(text)


async def sync_profiles(
    profiles: list[dict[str, Any]], python: str, server: str
) -> list[dict[str, Any]]:
    """Call the existing save and load MCP tools for every validated profile."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    parameters = StdioServerParameters(command=python, args=[server])
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            available = {tool.name for tool in (await session.list_tools()).tools}
            required = {"cad_save_drawing_profile", "cad_load_drawing_profile"}
            missing = sorted(required - available)
            if missing:
                raise RuntimeError(f"required profile tools are missing: {missing}")
            rows: list[dict[str, Any]] = []
            for profile in profiles:
                arguments = memory_tool_arguments(profile)
                saved = await _call_json(
                    session, "cad_save_drawing_profile", arguments
                )
                loaded = await _call_json(
                    session,
                    "cad_load_drawing_profile",
                    {"name": profile["name"]},
                )
                passed = (
                    loaded["name"] == profile["name"]
                    and loaded["unit"] == profile["unit"]
                    and loaded["layer_rules"]
                    == json.loads(arguments["layer_rules_json"])
                    and loaded["dimension_rules"]
                    == json.loads(arguments["dimension_rules_json"])
                )
                rows.append(
                    {
                        "name": profile["name"],
                        "saved_id": saved["id"],
                        "round_trip_passed": passed,
                    }
                )
            return rows


def main() -> None:
    """Validate profiles by default and sync them only with --apply."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--profile-dir", default=str(ROOT / "data" / "profiles")
    )
    parser.add_argument(
        "--python", default=str(ROOT / ".venv" / "Scripts" / "python.exe")
    )
    parser.add_argument("--server", default=str(ROOT / "src" / "server_memory.py"))
    args = parser.parse_args()
    profiles = load_profiles(args.profile_dir)
    if not args.apply:
        print(
            json.dumps(
                {
                    "mode": "dry-run",
                    "database_changed": False,
                    "profiles": [profile["name"] for profile in profiles],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    rows = asyncio.run(sync_profiles(profiles, args.python, args.server))
    print(
        json.dumps(
            {
                "mode": "apply",
                "all_passed": all(row["round_trip_passed"] for row in rows),
                "results": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
