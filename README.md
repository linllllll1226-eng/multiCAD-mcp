# multiCAD-mcp v0.4

`multiCAD-mcp` connects MCP-compatible AI clients to Windows CAD applications through COM. This release adds a guarded AutoCAD 2022 workflow for image reconstruction, verified drawing, persistent corrections, task tracking, task-scoped commit/revert operations, and optional local OCR for scanned drawings.

> This repository extends the Apache-2.0 project by [AnCode666/multiCAD-mcp](https://github.com/AnCode666/multiCAD-mcp). The original seven unified tools and multi-CAD adapters remain available.

## Current status

- **Primary verified target:** AutoCAD 2022 on Windows (`COM 24.1`).
- **MCP surface:** 23 tools: 7 upstream unified CAD tools plus 16 guarded workflow, memory, task, and vision tools.
- **Tests:** 251 automated tests at the v0.4 integration point.
- **Quality gate:** full Ruff lint/format checks and release-hygiene validation.
- **Transport:** local STDIO; no network listener is required for Codex.
- **Safe entry point:** `src/server_memory.py`.
- **Legacy entry point:** `src/server.py` (retained for upstream compatibility, without the complete guarded workflow).

The upstream adapters also target ZWCAD, GstarCAD, and BricsCAD. The enhanced validation, native dimension, XData, and task-lifecycle acceptance tests in this branch were performed against AutoCAD 2022; other CAD products may expose different COM properties.

## Guarded write workflow

Every enhanced write must follow:

```text
cad_plan_validate
        -> cad_execute_plan
        -> cad_verify_execution
```

The workflow validates units, geometry, sources, constraints, layers, uncertainty, and destructive intent before execution. It then reads real CAD entity data back from the drawing and records a verification receipt. A write is not considered successful merely because the planned tool calls completed.

Legacy write tools remain available for compatibility, but the included `autocad-drawing-assistant` Skill does not silently fall back to them when an enhanced step fails.

## Enhanced features

| Area | Capability |
|---|---|
| Drawing memory | User-confirmed corrections, drawing profiles, and execution results in local SQLite |
| Planning | Structured entities, constraints, confidence, source type, and uncertain items |
| Verification | Target/actual/error/pass comparison using real AutoCAD entity data |
| Dimension safety | Native diameter/radius dimensions, empty `TextOverride`, background fill checks |
| Task tracking | Stable `task_id`, persistent entity provenance, and AI-created-object lookup |
| Safe lifecycle | Verification-gated preview commit and task-scoped revert without global `UNDO` |
| Vision assistance | Vector PDF/raster preprocessing, local OCR, dimension evidence, cache, and benchmarks |
| Usability | Drawing profiles, write-before-backup helper, guarded template initializer, and one-click launcher |

## Requirements

- Windows
- Python 3.10+
- AutoCAD 2022 for the verified enhanced workflow
- `uv`
- Codex or another MCP-compatible client

## Install

```powershell
cd D:\AI\multiCAD-mcp
git switch release/cad-ai-v0.4
uv sync --extra dev --extra vision --extra docs --extra ocr
```

When this branch is published to a fork, replace the local checkout step with that fork's clone URL. Cloning the upstream repository alone does not currently provide these v0.4 extensions. For a minimal upstream-only installation, `uv sync` is sufficient. The `vision` extra installs OpenCV, NumPy, and PyMuPDF; the `ocr` extra adds PaddleOCR and local Paddle inference. No cloud OCR service is required.

## Configure Codex

Add the enhanced local STDIO server to `%USERPROFILE%\.codex\config.toml`:

```toml
[mcp_servers.autocad]
command = "D:\\AI\\multiCAD-mcp\\.venv\\Scripts\\python.exe"
args = ["D:\\AI\\multiCAD-mcp\\src\\server_memory.py"]
cwd = "D:\\AI\\multiCAD-mcp"
startup_timeout_sec = 30
tool_timeout_sec = 120
enabled = true
```

Adjust paths for your installation. Start AutoCAD and open a blank drawing or a saved copy before requesting a write.

Verify registration:

```powershell
codex mcp list
```

## Typical use

```text
Use $autocad-drawing-assistant.
Load the university_mechanical_drawing profile.
Analyze this image first. Use only explicit dimensions and uniquely derived
constraints. List uncertain geometry and do not draw until I confirm.
```

After confirmation:

```text
Start preview. Use the guarded three-stage workflow and verify every principal
dimension from real CAD entity data.
```

For scanned drawings, `cad_analyze_source` uses OCR by default. Vector PDFs keep
their more accurate embedded paths/text route. The first raster OCR request may
download official models; later identical analyses use the local result cache.

Convenience intents supported by the Skill include `分析这张图`, `开始预览`, `正式提交`, `检查图纸`, and `撤回本次`.

## Safety model

- Preview layers are used by default.
- `approximate_reference` geometry is rejected from formal outline layers.
- Destructive operations require explicit confirmation.
- Committed and reverted entities are selected by verified `task_id`, not the current selection or global `UNDO`.
- The local memory database is ignored by Git.
- Arbitrary export paths are disabled by default.
- The one-click launcher does not open, save, or modify a DWG automatically.
- Use a blank drawing for acceptance tests and a saved copy for real work.

## Tool groups

The seven upstream unified tools are:

```text
manage_session, draw_entities, manage_layers, manage_files,
manage_entities, manage_blocks, export_data
```

The enhanced entry point adds:

```text
cad_memory_search, cad_memory_add_correction, cad_memory_list,
cad_memory_delete, cad_save_drawing_profile, cad_load_drawing_profile,
cad_plan_validate, cad_execute_plan, cad_verify_execution,
cad_list_ai_tasks, cad_get_task_entities, cad_get_entity_provenance,
cad_commit_preview_task, cad_revert_ai_task,
cad_analyze_source, cad_vision_capabilities
```

## Test and quality checks

```powershell
uv run pytest -q -p no:cacheprovider
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mkdocs build --strict
uv run python scripts/check_repository_hygiene.py
```

The Windows CI workflow runs the test suite on Python 3.10, 3.11, and 3.12.

## Known limits

- Missing dimensions in a picture cannot become reliable manufacturing dimensions. Uncertain geometry must remain separate.
- OCR improves text evidence from scans but does not make ambiguous or missing dimensions authoritative.
- Current benchmarks are deterministic engineering fixtures, not a claim of universal recognition accuracy.
- Some arbitrary CAD edit/delete operations are deliberately unsupported by the guarded executor.
- Dimension layout still benefits from a visual audit after entity-level verification.
- An AutoCAD-integrated sidebar and voice panel are outside the v0.3 scope.

## Documentation

- [Documentation index](docs/README.md)
- [Memory and validation](docs/CAD_MEMORY_VALIDATION.md)
- [Task tracking and safe lifecycle](docs/CAD_TASK_TRACKING.md)
- [Safety hardening](docs/CAD_SAFETY_HARDENING.md)
- [Vision pipeline](docs/CAD_VISION_PIPELINE.md)
- [Scanned drawing OCR](docs/CAD_OCR.md)
- [Vision benchmark](docs/CAD_VISION_BENCHMARK.md)
- [Usability layer](docs/CAD_UX_IMPROVEMENTS.md)
- [Changelog](docs/03-CHANGELOG.md)

## License and attribution

Apache-2.0. Preserve the upstream copyright, license, and attribution when redistributing this derivative work.
