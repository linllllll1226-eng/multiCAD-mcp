"""Install a wheel in a fresh environment and verify it outside the checkout."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

PROBE = r"""
import asyncio, importlib, json, os, sys, sysconfig
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from cad_runtime import data_directory, resource_path
from cad_ux.profiles import load_profiles
from core.config import ConfigManager
from ui.resources import _load_template
from web.api import STATIC_DIR, api_app
from fastapi.testclient import TestClient

site = Path(sysconfig.get_paths()["purelib"]).resolve()
for name in ["server", "server_memory", "cad_runtime", "ui.resources", "web.api"]:
    module = importlib.import_module(name)
    assert Path(module.__file__).resolve().is_relative_to(site), (name, module.__file__)
for name in ["drawing_viewer.html", "layer_panel.html", "block_browser.html"]:
    assert "<html" in _load_template(name).lower()
assert (STATIC_DIR / "index.html").is_file()
assert len(load_profiles(resource_path("profiles"))) == 5
assert ConfigManager()._find_config_file() == resource_path("config.json")
assert ConfigManager()._config.dashboard.port == 8888
assert not data_directory().is_relative_to(site)
with TestClient(api_app) as client:
    assert client.get("/").status_code == 200
    assert client.get("/static/style.css").status_code == 200

async def check():
    params = StdioServerParameters(command=sys.executable, args=["-I", "-m", "server_memory"],
                                   env=dict(os.environ))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=20) as client:
            await client.initialize()
            tools = (await client.list_tools()).tools
            assert len(tools) == 25
            result = await client.call_tool("cad_vision_capabilities", {})
            assert not result.is_error
asyncio.run(asyncio.wait_for(check(), 40))
print(json.dumps({"wheel_smoke": "passed", "tools": 25, "profiles": 5,
                  "source_checkout_on_path": False, "cad_writes": 0}))
"""


def main() -> None:
    """Build a clean venv, install the artifact and check packaged runtime assets."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=False)
    environment = work / "venv"
    subprocess.run(["uv", "venv", "--python", sys.executable, str(environment)], check=True)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), str(args.wheel.resolve())], check=True
    )
    empty = work / "empty"
    empty.mkdir()
    env = {
        **os.environ,
        "MULTICAD_LOG_DIR": str(work / "logs"),
        "MULTICAD_DATA_DIR": str(work / "data"),
    }
    env.pop("PYTHONPATH", None)
    subprocess.run([str(python), "-I", "-c", PROBE], cwd=empty, env=env, check=True, timeout=90)


if __name__ == "__main__":
    main()
