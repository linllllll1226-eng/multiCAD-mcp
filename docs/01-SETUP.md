# Setup

## Prerequisites

- Windows with COM support
- Python 3.10+
- `uv`
- A supported CAD application

AutoCAD 2022 is the verified target for the enhanced validation, dimension, task-tracking, and commit/revert workflow. The legacy adapters also target ZWCAD, GstarCAD, and BricsCAD.

## Install

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
cd D:\AI\multiCAD-mcp
git switch release/cad-ai-v0.3
uv sync --extra dev --extra vision --extra docs
```

For a new machine, clone the fork that contains the v0.3 branch. The upstream repository alone does not currently contain these extensions.

Run the automated checks:

```powershell
uv run pytest -q -p no:cacheprovider
uv run ruff check src tests scripts --select E9,F63,F7,F82
```

## Enhanced Codex integration

Add the local STDIO server to `%USERPROFILE%\.codex\config.toml`:

```toml
[mcp_servers.autocad]
command = "D:\\AI\\multiCAD-mcp\\.venv\\Scripts\\python.exe"
args = ["D:\\AI\\multiCAD-mcp\\src\\server_memory.py"]
cwd = "D:\\AI\\multiCAD-mcp"
startup_timeout_sec = 30
tool_timeout_sec = 120
enabled = true
```

Change the paths to match your installation, restart Codex, and run:

```powershell
codex mcp list
```

`src/server_memory.py` registers 23 tools and is the supported entry point for the enhanced workflow. `src/server.py` remains the legacy upstream-compatible entry point.

## First safe test

1. Start AutoCAD 2022 and open a new blank drawing.
2. Ask Codex to read the document name, layer list, and object count without writing.
3. For a write, explicitly use the `autocad-drawing-assistant` Skill.
4. Require this sequence:

```text
cad_plan_validate -> cad_execute_plan -> cad_verify_execution
```

5. Confirm the target values against the real CAD entity values in the verification report.
6. Close the acceptance drawing without saving unless you deliberately want to keep it.

Do not use an original production DWG for the first test. Use a saved copy for later real work.

## Optional components

- `vision`: OpenCV, NumPy, and PyMuPDF for local evidence extraction.
- `docs`: MkDocs Material and mkdocstrings.
- `scripts/Start-CAD-AI.ps1`: checks AutoCAD, COM, the enhanced MCP entry, the local database, and the Codex Desktop launch target.
- `scripts/Backup-CAD-Drawing.py`: creates a timestamped backup before an authorized formal write.
- `scripts/Sync-CAD-Profiles.py`: synchronizes predefined drawing profiles.
- `D:\AI\CAD_Templates\Initialize-AI-DrawingTemplate.ps1`: guarded template initializer when present in the local installation.

## Development workflow

```powershell
git switch -c feature/short-description
uv run pytest -q -p no:cacheprovider
uv run ruff check src tests scripts --select E9,F63,F7,F82
```

Use conventional commit prefixes such as `feat:`, `fix:`, `docs:`, `test:`, and `release:`. Do not commit local SQLite databases, vision caches, DWG files, operator summaries, or timestamped script backups.
