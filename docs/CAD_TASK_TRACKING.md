# CAD task tracking and safe task operations

## Purpose

Each guarded `cad_execute_plan` run now receives a unique `task_id`. Every newly
created AutoCAD entity is marked with local XData under the registered application
name `CODEX_CAD_AI`. The same task, handle, layer, provenance, and verification
state are also recorded in `data/cad_memory.db`.

The feature adds five MCP tools:

- `cad_list_ai_tasks`
- `cad_get_task_entities`
- `cad_get_entity_provenance`
- `cad_commit_preview_task`
- `cad_revert_ai_task`

The active MCP remains local STDIO only. No network listener is added.

## Provenance

Owned creation entities store:

- `task_id`
- `created_by=codex_autocad_assistant`
- creation time
- drawing profile
- dimension source and confidence
- approximate-reference state
- execution result ID
- lifecycle status

Layout-only operations may reference an existing handle but never claim ownership
of that object. Commit and revert reject any handle whose XData does not prove the
requested task ownership.

## Guarded preview lifecycle

The drawing lifecycle is:

```text
cad_plan_validate
  -> cad_execute_plan (returns task_id and tagged preview handles)
  -> cad_verify_execution (verifies the same task_id)
  -> cad_commit_preview_task
```

`cad_commit_preview_task` accepts only a verified task. With `confirmed=false` it
returns a manifest and performs no write. With `confirmed=true` it changes only
the task entities' layers and provenance metadata. Geometry is read before and
after; a mismatch aborts the operation and restores prior layers and metadata.
`AI_UNCERTAIN` objects cannot be promoted to formal geometry layers.

## Reversible task withdrawal

`cad_revert_ai_task` does not issue a global AutoCAD Undo and does not hard-delete
objects. It moves only XData-proven objects for the requested task onto the hidden
`AI_REVERTED` layer. This preserves a recovery path and prevents accidental removal
of user-created objects or entities from another task.

A task whose post-execution verification failed may also be withdrawn when it still
has XData-proven created objects; a failed task is never eligible for formal commit.

The first call must use `confirmed=false` to return the exact manifest. A second
call with `confirmed=true` applies it. A formally committed task additionally
requires `allow_committed=true` after the user acknowledges that it is committed.

## Failure behavior

- CAD layer and XData edits are restored on operation failure.
- Entity-row and task-status database updates use one SQLite transaction.
- A task with missing or mismatched XData is blocked.
- Generic Undo and legacy delete/modify tools are never used as a fallback.
- Existing `cad_execute_plan` callers remain compatible; its successful response
  now also includes `task_id`, `execution_result_id`, and entity provenance rows.

## Tests

Run the focused tests without AutoCAD:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest `
  tests\unit\test_task_tracking.py `
  tests\unit\test_cad_memory.py -q
```

Real XData persistence and task operations must be accepted only in a new blank
AutoCAD 2022 drawing. The test must follow the guarded three-step workflow before
commit or revert.

The isolated acceptance helper creates its own AutoCAD instance, refuses existing
output paths, generates a blank test drawing, and saves/reopens it to prove XData
persistence:

```powershell
& '.\.venv\Scripts\python.exe' `
  '.\scripts\Run-CAD-Task-Tracking-Acceptance.py' `
  --reuse-existing-template
```

It never opens or modifies a user DWG.

## Rollback

The implementation branch is `feature/cad-task-tracking`. To return to the last
accepted usability layer without changing the active MCP command:

```powershell
git -C 'D:\AI\multiCAD-mcp' switch feature/cad-ux-improvements
```

The existing database file is retained. Old task tables are additive and do not
alter correction, profile, or execution-result records.
