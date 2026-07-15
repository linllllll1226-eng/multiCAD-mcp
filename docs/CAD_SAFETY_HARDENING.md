# CAD Safety and Performance Hardening

## Scope

This hardening layer applies to the enhanced STDIO entry point:

```text
src/server_memory.py
```

It strengthens the guarded write sequence without changing the original
`src/server.py` entry point:

```text
cad_plan_validate
-> cad_execute_plan
-> cad_verify_execution
```

## Validation receipts

`cad_plan_validate` now returns a short-lived, one-time
`validation_receipt.validation_id` when validation succeeds. The receipt is
bound to:

- the exact canonical plan hash;
- the active drawing identity;
- the active drawing `INSUNITS` value;
- a ten-minute lifetime by default.

Pass that exact ID to `cad_execute_plan` as `validation_id`. Execution fails
closed if the receipt is missing, expired, already consumed, or if the plan,
drawing, or drawing unit changed. Restarting the MCP process invalidates all
outstanding receipts.

The receipt proves that the executed plan is the plan that passed validation.
It does not independently authenticate the human who approved the plan. User
approval remains the responsibility of the Codex approval surface and the
`autocad-drawing-assistant` Skill.

## Drawing unit binding

The validator reads AutoCAD `INSUNITS` and compares it with `plan.unit`.

- A known mismatch blocks execution.
- `INSUNITS=0` (unitless) produces a warning and requires the user to confirm
  the intended unit.
- Unsupported plan units are rejected.

Do not change `INSUNITS` between validation and execution. Doing so invalidates
the receipt.

## Strict guarded-write mode

`server_memory.py` enables strict mode by default with:

```text
MULTICAD_STRICT_GUARDED_WRITES=1
```

In strict mode, legacy unified tools remain available for compatible read-only
operations, but direct legacy writes are blocked. The enhanced guarded workflow
is the only supported write path. Preview-layer creation remains available for
known `AI_PREVIEW_*` and `AI_UNCERTAIN` layers.

The original `server.py` entry point is unchanged. For controlled maintenance
only, strict mode can be disabled before starting a process:

```powershell
$env:MULTICAD_STRICT_GUARDED_WRITES = '0'
```

Do not disable strict mode for normal drawing work.

## Effective linetype verification

Layer creation accepts an explicit `linetype`. If needed, the adapter loads the
linetype from `acadiso.lin` and then `acad.lin`.

Post-execution verification resolves `ByLayer` entity linetypes to the owning
layer's effective linetype. Center objects must resolve to a center linetype and
hidden objects must resolve to a hidden linetype. A continuous line on a center
or hidden layer no longer passes merely because the layer name looks correct.

## Task and provenance pagination

Task reads now return compact summaries by default:

- `cad_list_ai_tasks`: default 20, maximum 100 tasks per page;
- `cad_get_task_entities`: default 50, maximum 200 entities per page;
- large `plan_data`, verification, actual entity data, and provenance are
  opt-in fields.
- live AutoCAD handle scans are disabled in task lists unless
  `include_active_counts=true`.

Use `offset` and `limit` to request the next page. This prevents large drawings
from filling the model context with thousands of entity records.

## Unicode-safe local output

The default export directory is now the repository-local `./exports` directory,
and arbitrary output paths are disabled by default. Override the directory with:

```text
MULTICAD_OUTPUT_DIR
```

Unicode paths are covered by automated tests. Keep exports within an explicitly
approved directory.

## Migration note

The enhanced execution call is intentionally stricter:

```text
cad_execute_plan(plan_json, validation_id)
```

Any Skill or client that previously called `cad_execute_plan(plan_json)` must be
updated to pass the receipt returned by `cad_plan_validate`.

## Tests

Run the unit suite with:

```powershell
D:\AI\multiCAD-mcp\.venv\Scripts\python.exe -m pytest tests\unit -q
```

The hardening suite covers receipt replay and tampering, drawing identity and
unit changes, strict legacy-write blocking, effective linetype validation,
Unicode output paths, task pagination, and existing memory/task workflows.

## Rollback

Return to the previously accepted task-tracking branch:

```powershell
git -C 'D:\AI\multiCAD-mcp' switch active/cad-task-tracking-20260714
```

The local SQLite database remains outside Git. Do not delete it during a code
rollback.
