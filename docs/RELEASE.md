# v0.5.0 release and wheel installation

Windows wheel installation is supported. Source-checkout PowerShell helpers
remain available from GitHub; they are not wheel console commands.

```powershell
uv sync --frozen --extra dev --extra vision --extra docs
uv build --no-sources
uv run python scripts/smoke_wheel.py --wheel dist/multicad_mcp-0.5.0-py3-none-any.whl --work-dir ../wheel-smoke-fresh
```

The smoke creates a fresh environment, installs the built wheel and runs from
an empty directory with isolated Python imports. It checks the UI templates,
dashboard index/CSS, bundled configuration, five profiles, guarded STDIO
startup, 25 tools and a read-only tool call. It does not start or write AutoCAD.

Install a downloaded wheel with `python -m pip install <wheel>`; configure your
MCP client to invoke the resulting `multicad-mcp.exe`, or the environment's Python
with arguments `["-I", "-m", "server_memory"]`. No checkout is required.
Install the vision extra when PDF/image analysis is needed.

Templates/static/config/profiles are immutable packaged inputs. Installed
runtime databases and caches default to %LOCALAPPDATA%/multiCAD-mcp/data;
MULTICAD_DATA_DIR overrides that root. Existing checkouts retain project/data.
MULTICAD_CAD_MEMORY_DB and MULTICAD_LOG_DIR remain supported overrides.
Copied runtime resources are checked against the authoritative checkout files.

Before tagging, require green Windows Python 3.10/3.11/3.12, security and CodeQL
checks on the exact commit. Use an annotated v0.5.0 tag pointing to that verified
commit; never retarget an existing release tag. Attach wheel/sdist and checksums.
PyPI publication is a separate action. The public release must not include
private drawings, databases, user images, screenshots or private acceptance data.
