# CAD Memory and Validation

## Purpose and compatibility

This feature adds an opt-in, local-only workflow for confirmed CAD experience,
structured drawing plans, pre-execution validation, guarded execution, and
post-execution verification.

The existing `src/server.py` entry point and all existing MCP tool signatures are
unchanged. The enhanced workflow uses `src/server_memory.py`. It runs only over
STDIO and does not start the dashboard or bind a TCP port.

## Components

- `cad_memory.database`: SQLite schema and local CRUD operations.
- `cad_memory.models`: structured drawing plan models.
- `cad_memory.validator`: unit, coordinate, dimension, layer, constraint, and
  destructive-operation checks.
- `cad_memory.executor`: validated preview execution with a best-effort AutoCAD
  undo mark.
- `cad_memory.verifier`: fresh COM reads and target-versus-actual comparison.
- `mcp_tools.tools.memory`: six local experience/profile MCP tools.
- `mcp_tools.tools.validation`: plan validation, guarded execution, and
  post-execution verification MCP tools.

## Database

Default path:

```text
D:\AI\multiCAD-mcp\data\cad_memory.db
```

The Python standard-library `sqlite3` module creates these tables:

- `corrections`
- `drawing_profiles`
- `execution_results`

Database files and SQLite sidecar files are ignored by Git. Do not copy, commit,
sync, or upload the `data` directory. Override the location only with the local
environment variable `MULTICAD_CAD_MEMORY_DB`.

Only a correction with `confirmed_by_user=true` is returned by default search and
list operations and may be treated as enforceable experience. Unconfirmed rows
remain informational and require `include_unconfirmed=true` to retrieve.

## Memory tools

### Add a correction

Call `cad_memory_add_correction` with category, trigger, wrong behavior, correct
behavior, optional JSON context, and an explicit `confirmed_by_user` value.

Example intent:

```json
{
  "category": "dimension",
  "trigger": "native diameter dimension",
  "wrong_behavior": "manual prefix produces ØØ15",
  "correct_behavior": "clear TextOverride and use the native diameter prefix",
  "context_json": "{\"autocad\": \"2022\"}",
  "confirmed_by_user": true
}
```

### Search and list

- `cad_memory_search` searches confirmed corrections by default.
- `cad_memory_list` lists `corrections`, `drawing_profiles`, or
  `execution_results`.
- Set `include_unconfirmed=true` only for review; do not enforce those rows.

### Delete

Call `cad_memory_delete` with the table, record ID, and `confirmed=true`. A delete
without explicit confirmation is rejected.

### Drawing profiles

- `cad_save_drawing_profile` creates or updates a named unit/layer/dimension
  profile.
- `cad_load_drawing_profile` returns the named profile.

## Structured drawing plans

Every entity contains:

- `entity_type`
- `coordinates`
- `dimensions`
- `layer`
- `linetype`
- `dimension_source`
- `confidence`
- `constraints`
- `uncertain_items`

Allowed dimension sources are:

- `explicit_dimension`
- `geometric_constraint`
- `user_confirmed`
- `approximate_reference`

An `approximate_reference` entity is rejected unless its layer is
`AI_UNCERTAIN`. In preview mode, new objects must use one of:

- `AI_PREVIEW_OUTLINE`
- `AI_PREVIEW_CENTER`
- `AI_PREVIEW_HIDDEN`
- `AI_PREVIEW_HATCH`
- `AI_PREVIEW_DIM`
- `AI_UNCERTAIN`

The plan must identify drawing units, existing layers, uncertainty, and explicit
user confirmation. Delete and overwrite requests also require their dedicated
allow flags.

## Validation workflow

1. Read the active DWG, unit, layers, and object statistics with existing read
   tools.
2. Search only confirmed corrections relevant to the task.
3. Build a JSON `DrawingPlan` and show it to the user.
4. After the user confirms, set `user_confirmed=true`.
5. Call `cad_plan_validate`. It checks the active AutoCAD layer list and blocks
   invalid plans.
6. Call `cad_execute_plan`. It validates again immediately before writing and
   returns created handles.
7. Call `cad_verify_execution` with the same plan and returned handles.
8. Report the comparison rows: target, actual, error, and pass/fail.

Validation covers missing coordinates or units, nonpositive dimensions, missing
layers, circle/arc/dimension parameters, symmetry, concentricity, tangency, equal
distance, uniform distribution (including three points at 120 degrees), dimension
chains, uncertainty, and destructive intent.

## Dimension safeguards

- Use `diametric_dimension` for a native `AcDbDiametricDimension`.
- Use `radial_dimension` for a native `AcDbRadialDimension`.
- Keep `text_override` empty so AutoCAD produces `Ø` or `R` once.
- Background fill is rejected by default.
- Dimension layout uses the separate `layout_only` operation and records measured
  geometry points before and after moving text.
- Post-verification reads `Measurement`, `TextOverride`, and background-fill state
  from the real AutoCAD object.

## Running tests

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_cad_memory.py tests\unit\test_plan_validator.py tests\unit\test_plan_executor.py tests\unit\test_post_verifier.py -v
```

These tests do not require AutoCAD. Run integration tests only against a newly
created blank DWG after confirming the active document name and object count.

## Starting the enhanced server

Candidate STDIO command:

```powershell
D:\AI\multiCAD-mcp\.venv\Scripts\python.exe D:\AI\multiCAD-mcp\src\server_memory.py
```

Do not replace the existing Codex MCP configuration until this command and the
blank-DWG integration test have been approved.

## Disable or roll back

To disable memory and validation without changing code, continue using:

```powershell
D:\AI\multiCAD-mcp\.venv\Scripts\python.exe D:\AI\multiCAD-mcp\src\server.py
```

To discard the feature branch, first switch away from it, then delete only the
feature branch after reviewing its changes. Restore the timestamped Codex
`config.toml.backup-cad-memory-*` file only if the active configuration was later
changed. The initial implementation does not change the active Codex config.

## Protect real drawings

- Use a blank test DWG for the first integration run.
- Confirm active DWG name, units, layers, and entity counts before any write.
- Keep `preview_mode=true` and use preview layers.
- Never set `allow_delete` or `allow_overwrite` without a separate explicit user
  confirmation that names the target handles.
- Keep uncertain geometry on `AI_UNCERTAIN` and never attach invented dimensions.
- Save or overwrite a DWG only after a separate explicit confirmation.
- Verify created handles from actual AutoCAD data before reporting success.

## Known limits

- Guarded execution currently supports create operations for lines, rectangles,
  circles, arcs, polylines, aligned/linear dimensions, native diametric dimensions,
  and native radial dimensions, plus dimension text-position layout.
- Arbitrary modify and delete plans can be validated but are intentionally not
  auto-executed by the guarded executor.
- Existing legacy write tools remain available for backward compatibility; the
  safety guarantees apply when clients use the structured `cad_*` workflow.
- COM property availability can vary between CAD products and entity types; missing
  properties appear as failed verification rows rather than inferred values.
