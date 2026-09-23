# 04 - Troubleshooting

## Quick Reference

| Error | Cause | Solution |
|-------|-------|----------|
| "Not connected" | CAD not running | Start CAD application |
| "Connection failed" | COM issue | Reinstall pywin32 |
| "Invalid coordinate" | Bad format | Use "x,y" or "x,y,z" |
| "Document not available" | CAD closed | Restart CAD |
| "Permission denied" | File locked | Check permissions |

## Connection Issues

### Server startup fails while opening its log file

File logs default to `logs/multicad_mcp.log` inside the project. For a read-only
deployment, set `MULTICAD_LOG_DIR` to a directory your account can write before
starting the server, or set it in the MCP client's server environment:

```powershell
$env:MULTICAD_LOG_DIR = Join-Path $env:LOCALAPPDATA 'multiCAD-mcp\logs'
```

If directory creation or opening the UTF-8 log file fails, the server warns on
stderr and continues without file logging. Standard output remains available for
the stdio MCP protocol. This handles startup log-file access errors; it does not
change filesystem permissions or recover a file handler that fails later while
writing. The override applies to both `server.py` and `server_memory.py`.

### "Connection failed: AutoCAD.Application"

```powershell
# Reinstall pywin32
uv run python -m pip install --upgrade pywin32

# Verify COM
uv run python -c "import win32com.client; print('OK')"
```

- Ensure CAD is running
- Check CAD version (AutoCAD 2018+, ZWCAD 2020+)

### "Not connected"

Normal on startup - server auto-connects on first tool call.

If it fails:
1. Check CAD is running
2. Restart CAD
3. Use `manage_session` with `{"action": "connect"}`

## Operation Failures

### Workflow

### Before Committing
1. `uv run pytest -q -p no:cacheprovider` - All release tests must pass
2. `uv run ruff check src tests scripts --select E9,F63,F7,F82` - Critical lint must pass
3. `uv run mkdocs build --strict` - Documentation must build when docs dependencies are installed

### Drawing not visible

```python
zoom_extents()     # Fit view
list_layers()      # Check layer visibility
```

### Invalid coordinates

**Valid**:
```
"0,0"  "100.5,50.25"  "-10,-20"  "0,0,0"
```

**Invalid**:
```
"0, 0"   # Space
"(0,0)"  # Parentheses
"a,b"    # Non-numeric
```

## Configuration Issues

### Changes not taking effect

- Restart server after editing `src/config.json`
- Verify JSON syntax: `python -m json.tool src/config.json`

## Debug Tools

### Enable debug logging

Edit `src/config.json`:
```json
{"logging_level": "DEBUG"}
```

View logs:
```powershell
# Setup
uv sync --extra dev --extra vision
uv run python -m pip install --upgrade pywin32

# Run
uv run python src/server_memory.py

# Test
uv run pytest -q -p no:cacheprovider
npx -y @modelcontextprotocol/inspector uv run python src/server_memory.py

# Quality
uv run ruff check src tests scripts --select E9,F63,F7,F82
```

Browse to `http://localhost:8888` to test tools interactively.

### Test connection directly

```python
from adapters import AutoCADAdapter
adapter = AutoCADAdapter("autocad")
adapter.connect()
print(f"Connected: {adapter.is_connected()}")
```

## Common Fixes

### ModuleNotFoundError

```powershell
uv sync --dev
uv run python -m pip install --upgrade pywin32
```

### Execution policy error

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```
