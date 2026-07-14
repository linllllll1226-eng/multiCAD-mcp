# CAD usability layer

This branch adds optional launch, profile, template, backup, and Skill helpers around the accepted enhanced workflow. It does not change `src/server_memory.py`, the plan validator, the executor, or the verifier.

## Activation state

The files may be installed and tested without touching a DWG. Until the user explicitly enables the feature:

- do not run `Sync-CAD-Profiles.py --apply`;
- do not copy the Skill candidate into the user Skill directory;
- do not run the template initializer against AutoCAD;
- do not change the Codex MCP configuration.

The desktop shortcut only points to `scripts/Start-CAD-AI.ps1`; creating it does not execute the script.

## One-click start

`scripts/Start-CAD-AI.ps1`:

1. reads an existing AutoCAD COM object or starts AutoCAD 2022 without a DWG argument;
2. waits for the AutoCAD 2022 COM version (`24.1`) to become available;
3. verifies that the `autocad` section in the user Codex config contains `server_memory.py`;
4. opens `data/cad_memory.db` read-only and runs `PRAGMA quick_check`;
5. starts Codex only when all checks pass.

It never calls AutoCAD `Open`, `Save`, `SaveAs`, drawing, or entity-edit methods.

## Drawing profiles

Preset JSON files live in `data/profiles`. Each file contains units, layer names, ACI colors, linetypes, text height, dimension rules, allowed tolerance, preview policy, and a default save directory.

Validate without changing the database:

```powershell
& '.venv\Scripts\python.exe' 'scripts\Sync-CAD-Profiles.py'
```

After explicit user confirmation, register and round-trip every profile through the existing `cad_save_drawing_profile` and `cad_load_drawing_profile` MCP tools:

```powershell
& '.venv\Scripts\python.exe' 'scripts\Sync-CAD-Profiles.py' --apply
```

No new profile MCP API is introduced.

## Template preparation

`scripts/Initialize-AI-DrawingTemplate.ps1` defaults to a dry run. After explicit confirmation it may be run with `-ApplyToBlankDrawing`, but it refuses any active document that is not an empty, unsaved `Drawing*.dwg`.

The initializer prepares these layers:

- `OUTLINE`, `CENTER`, `HIDDEN`, `HATCH`, `DIM`, `TEXT`;
- `AI_PREVIEW_OUTLINE`, `AI_PREVIEW_CENTER`, `AI_PREVIEW_HIDDEN`, `AI_PREVIEW_HATCH`, `AI_PREVIEW_DIM`, `AI_UNCERTAIN`.

It also loads `CENTER2` and `HIDDEN2`, creates `AI_STANDARD`, prepares `AI_STANDARD_DIM`, and sets an initial text height of 3.5 mm. It does not create geometry or save a file. After visual review and a separate confirmation, save manually as `D:\AI\CAD_Templates\AI_Drawing_Template.dwt`.

## Formal-write backup

Run the following immediately before a formal CAD write:

```powershell
& '.venv\Scripts\python.exe' 'scripts\Backup-CAD-Drawing.py' --keep 20
```

The helper uses `GetActiveObject` only. It blocks if the drawing has never been saved, has unsaved changes, is not a DWG, or the on-disk file is inaccessible. A valid DWG is copied to `AI_Backups` beside the source. Retention deletion is restricted to older files matching that source drawing's own `*.AI_BACKUP.*.dwg` pattern; the original and unrelated files are never deleted.

## Shortcut intent safety

The candidate Skill routes analysis, preview, formal commit, inspection, and task-scoped withdrawal. All geometry writes still require:

1. `cad_plan_validate`;
2. `cad_execute_plan`;
3. `cad_verify_execution`.

The accepted core currently supports creation and dimension layout, but intentionally rejects general modify/delete plans. Therefore a formal preview-to-layer move or task-scoped undo must stop safely when the core cannot prove the target operation. The Skill must not fall back to legacy entity modification or a generic undo that could affect user work.

## Rollback

Before activation, switch back to `feature/cad-memory-validation` and remove only the untracked desktop shortcut if desired. After activation, restore the backed-up user Skill, omit the profile JSON sync, and continue using the unchanged `server_memory.py` MCP entry. No DWG rollback is needed because implementation and unit testing do not modify a drawing.
