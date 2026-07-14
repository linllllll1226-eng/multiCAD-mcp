---
name: autocad-drawing-assistant
description: Safely read, analyze, draw, dimension, copy, and modify AutoCAD 2022 DWG drawings through the connected multiCAD-mcp server, enforcing cad_plan_validate, cad_execute_plan, and cad_verify_execution for writes. Use for image, PDF, or text-based CAD tasks and the shortcut intents 分析这张图, 开始预览, 正式提交, 检查图纸, and 撤回本次.
---

# AutoCAD Drawing Assistant

Use the currently connected `multiCAD-mcp` CAD tools to work on the active AutoCAD 2022 drawing. Preserve existing work, distinguish confirmed geometry from approximations, and verify results from real CAD entity data.

## Establish a safe connection

- Confirm that `multiCAD-mcp` can reach the active AutoCAD document before planning any CAD operation.
- Diagnose an unavailable MCP or AutoCAD COM connection before drawing.
- Operate only on the active drawing named in the preflight report.
- Do not save, overwrite, close, or replace a DWG unless the user explicitly authorizes that action.
- Use CAD tools exposed through `multiCAD-mcp` for drawing and editing. Do not substitute arbitrary operating-system or shell commands for CAD operations.

## Enforce the guarded write workflow

- Before any CAD geometry creation, modification, deletion, copy, or dimension-layout write, confirm that these enhanced tools are available: `cad_plan_validate`, `cad_execute_plan`, and `cad_verify_execution`.
- Use legacy CAD tools for read-only preflight and inspection. Do not default to `draw_entities`, modifying `manage_entities`, write operations in `manage_files`, or other legacy write paths for geometry.
- Search `cad_memory_search` for relevant confirmed corrections before finalizing the plan. Treat only records with `confirmed_by_user=true` as enforceable experience.
- Execute every approved CAD write in this exact order:
  1. Call `cad_plan_validate` with the complete structured plan.
  2. Stop without writing if validation does not return `passed=true`.
  3. Call `cad_execute_plan` with the same unchanged plan.
  4. Stop and report the failure if execution does not return `success=true`.
  5. Call `cad_verify_execution` with the same plan and the returned handles.
  6. Report success only when verification returns `passed=true` and every required comparison row passes.
- Never silently fall back to an old write tool when an enhanced workflow tool is unavailable or cannot express the requested operation. Stop and ask the user for direction.
- Permit `manage_layers` only to create missing, user-approved preview layers before guarded geometry execution. Do not use this exception to create, move, delete, or alter geometry.
- Keep the plan payload unchanged between validation, execution, and verification. If any coordinate, dimension, layer, linetype, constraint, uncertainty, or target handle changes, validate the revised plan again.

## Plan before drawing

1. Read and report the current DWG name, drawing units, layers, and object statistics.
2. Analyze every image, PDF, and written requirement supplied by the user.
3. Classify the available information into:
   - dimensions explicitly marked in the source;
   - dimensions uniquely derivable from stated or visible constraints such as concentricity, symmetry, tangency, equal spacing, and uniform distribution;
   - content that cannot be uniquely determined.
4. Never derive a formal production dimension from an image's pixel scale.
5. Ask the user about unresolved content, or propose placing it on `AI_UNCERTAIN` as an approximate reference. Give uncertain geometry a clearly distinct color and linetype, and never attach invented dimensions to it.
6. Present a drawing plan that lists the intended CAD tools, layers, objects, coordinates, dimensions, inferred relationships, and unresolved items.
7. Wait for explicit user confirmation of the plan before creating any CAD entity.

## Route shortcut intents

- For **“分析这张图”**, perform only the read-only DWG preflight and source analysis. Classify explicit, uniquely derived, and uncertain information; do not create, move, delete, dimension, save, or back up anything.
- For **“开始预览”**, load the user-selected profile with `cad_load_drawing_profile`, require explicit plan confirmation, force new geometry onto `AI_PREVIEW_*` or `AI_UNCERTAIN`, then run `cad_plan_validate` → `cad_execute_plan` → `cad_verify_execution` without changing the plan payload. Preserve the returned `task_id` and pass that same ID to verification.
- For **“正式提交”**, require a passed preview verification and identify the exact `task_id`. Before any write, run `D:\AI\multiCAD-mcp\.venv\Scripts\python.exe D:\AI\multiCAD-mcp\scripts\Backup-CAD-Drawing.py --keep 20`; stop if it reports an unsaved or dirty drawing. Call `cad_commit_preview_task` first with `confirmed=false` and show the returned handles, object types, source layers, and target layers. After a fresh user confirmation, call it again with `confirmed=true` and the same mapping. This operation may change only layers and provenance metadata; it must not move, scale, reconstruct, or delete geometry. Never fall back to a legacy modification tool.
- For **“检查图纸”**, inspect read-only for duplicate geometry, open polylines that should be closed, wrong layers, abnormal dimension type or measurement, non-empty `TextOverride`, text fill or masks, and incorrect center/hidden linetypes. Report handles and evidence; do not repair unless the user confirms a separate plan.
- For **“撤回本次”**, call `cad_list_ai_tasks` and identify the exact `task_id`; never infer ownership from the current selection. Call `cad_revert_ai_task` first with `confirmed=false` and show its manifest. After confirmation, call it with `confirmed=true`. The safe revert moves only XData-proven objects from that task to the hidden `AI_REVERTED` layer; it does not use global Undo and does not hard-delete objects. A committed task additionally requires the user to confirm `allow_committed=true`. Never use generic Undo or legacy deletion as a fallback.

## Draw in classified preview layers

Use these layers by default during preview work:

- `AI_PREVIEW_OUTLINE` for formal visible outlines;
- `AI_PREVIEW_CENTER` for centerlines;
- `AI_PREVIEW_HIDDEN` for hidden lines;
- `AI_PREVIEW_HATCH` for section hatching;
- `AI_PREVIEW_DIM` for dimensions;
- `AI_UNCERTAIN` for approximate reference geometry that lacks unique dimensions.

Keep outlines, centerlines, hidden lines, hatching, and dimensions separated by layer and object purpose.

Before deleting, overwriting, batch-moving, or batch-modifying any existing object, describe the exact target objects and request a fresh confirmation. Preserve all other objects.

Group each approved operation into one undoable unit whenever the available CAD interface supports an undo mark or equivalent transaction. Do not claim atomic undo support when the tool does not expose it.

## Create dimensions correctly

- Create a diameter callout as a true **Diametric Dimension**.
- Create a radius callout as a true **Radial Dimension**.
- Keep `TextOverride` empty by default for diameter and radius dimensions.
- Let the dimension object generate its own diameter or radius prefix. Never manually add `Ø`, `%%c`, or `R` to a true diameter or radius dimension.
- Create ordinary distances as true linear or aligned dimensions, and keep their `TextOverride` empty by default.
- Keep dimension text color `ByLayer` and prefer the text style and text height used by normal, readable dimensions in the current drawing.
- Do not enable text background fill, background masking, or an MText/MLeader text box unless the user explicitly requests it.
- Never alter measured points, extension-line origins, geometry coordinates, or the measured value merely to improve dimension layout.
- When dimension text overlaps an outline, centerline, hidden line, or another dimension, move only the dimension text and dimension-line position.
- Run `REGEN` after dimension or linetype display changes when the CAD interface supports it.

## Verify the completed work from CAD data

1. Re-read every newly created or modified CAD object. Do not infer success from the plan, a screenshot, or the intended command.
2. For every major dimension, report a comparison containing:
   - target value;
   - actual CAD measurement;
   - difference;
   - pass or fail.
3. Check each relevant object's layer, linetype, closed state, and real CAD object type.
4. Confirm object counts and identify which objects were created, modified, or left unchanged.
5. List every unresolved item and every item that still needs human visual inspection.
6. Report the drawing as successful only when the actual CAD measurements and required properties pass verification. Otherwise report the mismatch precisely and propose a safe correction plan before changing anything further.

## Prevent known failures

- If dimension text appears as a solid colored block, inspect text background fill, background mask, MText fill, MLeader text frames, font availability, and text style. Disable unintended fill or masking without changing geometry.
- Prevent `ØØ15` by using a true Diametric Dimension with an empty `TextOverride`; clear any manual diameter prefix.
- Prevent `RR15` by using a true Radial Dimension with an empty `TextOverride`; clear any manual radius prefix.
- If a 7 mm dimension overlaps a 12 mm dimension or other geometry, reposition only its text and dimension line. Keep the measured points and actual value unchanged.
- Before dimension-layout cleanup, record the coordinates and properties of non-target geometry; re-read and compare them afterward to ensure no geometry moved.
- Never promote an outline without sufficient dimensions to a precise outline. Keep it on `AI_UNCERTAIN`, label it as approximate, and do not invent dimensions.
- Use `Continuous` for visible outlines, an appropriate center linetype such as `CENTER2` for centerlines, and an appropriate hidden linetype such as `HIDDEN2` for hidden lines. Load missing standard linetypes through CAD when available.
- If centerlines or hidden lines are unclear, adjust drawing `LTSCALE` or object linetype scale reasonably, run `REGEN`, and verify their actual layer and linetype afterward. Do not change geometry to make a linetype visible.

## Report each task

Provide a concise record of:

1. preflight DWG state;
2. confirmed plan and uncertainty classification;
3. objects actually created or modified;
4. verification results with target, actual value, difference, and status;
5. layers, linetypes, object types, and closed states;
6. unresolved and manual-check items;
7. save status, without saving unless authorized.
