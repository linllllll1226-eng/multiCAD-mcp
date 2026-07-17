# Background visual audit / 后台视觉审核

`cad_render_task_audit` is the primary true-background screenshot/audit path. It
reads the actual entities owned by one `task_id` and
renders them to local SVG and PNG files. It never captures the AutoCAD window,
activates a document, changes the view, calls Zoom/REGEN, or writes to the DWG.

`cad_render_task_audit` 会读取指定 `task_id` 所拥有的真实 CAD 图元，并在本地
离屏生成 SVG 与 PNG。它不截取 AutoCAD 窗口、不激活图纸、不修改视口、
不调用 Zoom/REGEN，也不写入 DWG。

## Why / 为什么

When hardware acceleration is enabled, a minimized or covered AutoCAD window
may stop presenting fresh pixels. A normal screenshot can therefore be black,
stale, or unavailable even though COM can still read the drawing database.

启用硬件加速时，最小化或被遮挡的 AutoCAD 可能停止刷新屏幕像素。普通截图
因此可能黑屏、过期或完全失败，但 COM 仍可读取图纸数据库。

## Usage / 使用

```text
cad_render_task_audit(
  task_id="cad_...",
  source_path="D:\\drawings\\reference.pdf",
  source_page=1,
  expected_manifest_json="{...}"
)
```

The result contains:

- `png_path` and `svg_path` for model or human review;
- `world_bounds` and entity/layer counts;
- duplicate geometry and degenerate entity warnings;
- source-derived required segment/circle/annotation/count/view-region completeness checks;
- an optional `source_vs_cad.png` side-by-side image;
- `background_safe=true` and `window_capture_used=false` evidence.

Default output:

```text
data/audit_reports/<task_id>/audit.png
data/audit_reports/<task_id>/audit.svg
```

Set `MULTICAD_AUDIT_OUTPUT_ROOT` to redirect local output. UNC/network roots
are rejected. Output files are review artifacts only and do not alter task or
DWG state.

## Recommended review sequence / 推荐审核顺序

1. Run `cad_verify_execution` to verify target values against real entities.
2. Run `cad_render_task_audit` while AutoCAD may remain in the background.
3. Review the PNG against the source image/PDF for missing lines, wrong view
   structure, overlaps, and annotation crowding.
4. If a correction is needed, create a new guarded plan and use
   `cad_plan_validate -> cad_execute_plan -> cad_verify_execution`.

The manifest should be derived from explicit source geometry before drawing.
When any required feature is absent, `manifest_comparison.passed=false`; the
assistant must not report the task complete. Pixel geometry remains a candidate
and must not become an invented production dimension.

Required source text belongs in `required_annotations`. Each entry can require
exact or substring text, a layer, a minimum count, and optional view bounds:

```json
{
  "required_annotations": [
    {"text": "THRU", "layer": "AI_PREVIEW_DIM"},
    {"text": "DEPTH 65", "bounds": [100, 0, 180, 90]},
    {"text": "ALL DIMENSIONS ARE IN MILLIMETERS", "match": "contains"}
  ]
}
```

If any required annotation is missing, the manifest fails even when every line,
circle, and numeric CAD measurement is correct.

## Incident review: missing parameters / 漏标复盘

The Anchor Slide reconstruction exposed a completion-gap rather than a geometry
calculation error:

1. the source parser produced geometry and dimension candidates, but the audit
   manifest described only lines, circles, counts, and view regions;
2. guarded CAD verification correctly proved the entities that were created,
   but it could not prove that every source note had been created;
3. the off-screen renderer displayed plain text but did not treat missing text
   as a failed completeness gate;
4. the task could therefore pass geometry checks while omitting `THRU`,
   `DEPTH 65`, keyway depth, angle notes, and the general millimetre note.

The prevention rule is now explicit: build a source inventory before drawing,
place every required note/callout in `required_annotations`, then require all
three gates before completion: real CAD entity verification, manifest-backed
off-screen comparison, and one live-window presentation review. OCR candidates
marked `needs_confirmation=true` remain uncertain and cannot become formal
dimensions without source or user confirmation.

本次问题不是几何计算错误，而是“完成”的定义不够严格：旧清单能证明已创建对象
正确，却不能证明源图里的每条参数文字都已创建。现在源图中必须出现的说明、孔深、
通孔、角度和通用备注都要进入 `required_annotations`；任何一项缺失都会令完整性审核
失败，不能再报告“画完”。

## Optional real UI capture / 可选真实 CAD 界面截图

The existing `manage_session` action `screenshot` now uses a stronger discovery
and capture chain:

1. AutoCAD COM `Application.HWND` when available;
2. active DWG title, `acad.exe`, CAD title terms, and real-world window classes
   as ranked fallbacks; `AfxMDIFrame*` is preferred and AutoCAD text/command
   history windows are rejected;
3. `PrintWindow(PW_RENDERFULLCONTENT)` so another window may cover AutoCAD;
4. screen-crop fallback;
5. pixel variance validation so black/stale-looking captures are not reported
   as successful.

`manage_session(action="screenshot")` will not move, focus, or restore the window
by default. Use `allow_restore=true` only when you explicitly permit a temporary
restore/focus switch. A minimized hardware-accelerated viewport may not expose
fresh pixels at all; this never blocks `cad_render_task_audit`, which remains the
deterministic background review path.

`cad_capture_live_window` exposes the same HWND capture as a standalone read-only
MCP tool. It does not require a usable AutoCAD COM proxy, so it can still capture
the visible UI when COM registration or thread isolation prevents normal adapter
connection. It returns a local image path instead of a large base64 payload for
lower latency and token use.

When Windows leaves a restored AutoCAD window at virtual minimized coordinates
such as `(-21333, -21333, ...)`, the capture helper waits for a valid rectangle
and, when `allow_restore=true`, recovers the recorded `WindowPlacement` before
capturing. This fixes the case where AutoCAD is available through COM but its
main window rectangle is temporarily unusable.

现在真实界面截图不再只依赖固定窗口类名。系统优先读取 COM HWND，并综合当前
DWG 标题、`acad.exe` 进程、CAD 标题和 `AfxMDIFrame*` 类名选出主绘图窗口，
明确排除文本/命令历史窗口；随后使用 `PrintWindow` 截图并检查图像是否为黑屏或
近似纯色。默认不会移动、聚焦或恢复窗口，只有显式传入 `allow_restore=true`
才允许短暂改变窗口状态。真正的后台审核始终优先使用 `cad_render_task_audit`。
