# CodeQL quality review

Baseline: `bea1eab0fd8fc867398a9c39583639aa8c338bd5` (2026-10-15).

The baseline contained 128 open findings: 74 type-declaration placeholders and 54 other findings, including 37 empty exception handlers. This change reviews every non-declaration finding. It does not disable queries, dismiss alerts, modify drawings, or change the CAD source-completeness acceptance gates.

## Behavior changes

- An unreadable task-owned entity makes the active count unknown (`null`), with the observed subtotal and per-handle read errors. A subsequent complete read can report a count again.
- Invalid or unavailable exported geometry is `null` in extraction results and blank in Excel, with a `GeometryErrors` map on the row. Genuine measured zero remains zero; valid endpoint/alternate-length observations can recover a missing length. Nonapplicable geometry fields retain their existing zero convention.
- Drawing switching reads back the active name and saved path before caching success. An unsupported setter is tolerated only when independent activation actually succeeded.
- Refused deletion of an existing selection set stops recreation. Missing-set lookup and final cleanup remain observable best-effort operations.
- Optional display updates and undo-mark cleanup keep their fallback behavior with diagnostics. The original operation failure is not replaced by a cleanup failure.
- UTF-8 stream configuration handles embedded stream wrappers independently. Legacy malformed persisted JSON retains its raw value with a field-name-only warning.

## Declaration findings retained

All 74 baseline “statement has no effect” findings are ellipsis declarations: 62 within `if TYPE_CHECKING`, and 12 protocol methods. They describe type interfaces. This change preserves those interfaces and does not suppress their query. Confirm the final branch analysis separately; this ledger records source decisions rather than claiming main has already been rescanned.

## Per-finding decisions

Locations below refer to the baseline, so later line movement does not change the reviewed identity.

| Alert | Rule | Baseline location | Decision |
| --- | --- | --- | --- |
| [#6](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/6) | `py/conflicting-attributes` | `src/adapters/autocad_adapter.py:49` | Explicitly select the existing concrete providers for all 24 methods/properties repeated in mixin typing declarations, retaining their function/property descriptors and runtime dispatch. |
| [#7](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/7) | `py/empty-except` | `scripts/Run-CAD-Task-Tracking-Acceptance.py:483` | Keep best-effort temporary-resource cleanup, with a contextual diagnostic and no masking of a primary failure. |
| [#8](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/8) | `py/empty-except` | `src/adapters/mixins/block_mixin.py:438` | Keep best-effort temporary-resource cleanup, with a contextual diagnostic and no masking of a primary failure. |
| [#9](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/9) | `py/empty-except` | `src/adapters/mixins/block_mixin.py:446` | Keep best-effort temporary-resource cleanup, with a contextual diagnostic and no masking of a primary failure. |
| [#10](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/10) | `py/empty-except` | `src/cad_memory/database.py:55` | Preserve legacy raw JSON data and log the field name without exposing its contents. |
| [#11](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/11) | `py/empty-except` | `src/adapters/mixins/drawing_mixin.py:750` | Keep the optional observation/presentation fallback and record why it was unavailable. |
| [#12](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/12) | `py/empty-except` | `src/adapters/mixins/drawing_mixin.py:764` | Keep the optional observation/presentation fallback and record why it was unavailable. |
| [#13](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/13) | `py/empty-except` | `src/adapters/mixins/drawing_mixin.py:774` | Keep the optional observation/presentation fallback and record why it was unavailable. |
| [#14](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/14) | `py/empty-except` | `src/adapters/mixins/drawing_mixin.py:820` | Keep the optional observation/presentation fallback and record why it was unavailable. |
| [#15](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/15) | `py/empty-except` | `src/adapters/mixins/drawing_mixin.py:918` | Keep the optional observation/presentation fallback and record why it was unavailable. |
| [#17](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/17) | `py/empty-except` | `src/cad_memory/executor.py:50` | Log optional undo-mark cleanup failure without masking the original operation exception. |
| [#18](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/18) | `py/empty-except` | `src/cad_memory/executor.py:166` | Log a nonfatal view refresh failure without invalidating entity creation results. |
| [#19](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/19) | `py/empty-except` | `src/adapters/mixins/file_mixin.py:205` | Read back the active drawing name/path before changing the cached document. |
| [#20](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/20) | `py/empty-except` | `src/adapters/mixins/file_mixin.py:216` | Keep the optional observation/presentation fallback and record why it was unavailable. |
| [#21](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/21) | `py/empty-except` | `src/adapters/mixins/export_mixin.py:288` | Keep best-effort temporary-resource cleanup, with a contextual diagnostic and no masking of a primary failure. |
| [#22](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/22) | `py/empty-except` | `src/adapters/mixins/export_mixin.py:297` | Keep best-effort temporary-resource cleanup, with a contextual diagnostic and no masking of a primary failure. |
| [#23](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/23) | `py/empty-except` | `src/adapters/mixins/export_mixin.py:389` | Export invalid circle radius and derived values as unknown with explicit geometry errors. |
| [#24](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/24) | `py/empty-except` | `src/adapters/mixins/export_mixin.py:405` | Export invalid arc radius as unknown with explicit geometry errors. |
| [#25](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/25) | `py/empty-except` | `src/adapters/mixins/export_mixin.py:411` | Export invalid arc length/circumference as unknown with explicit geometry errors. |
| [#26](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/26) | `py/empty-except` | `src/adapters/mixins/export_mixin.py:439` | Validate endpoint distance fallback; preserve real measured zero when optional fallback fails. |
| [#27](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/27) | `py/empty-except` | `src/adapters/mixins/export_mixin.py:452` | Validate line/polyline/spline measurements; recover through independent observations when valid. |
| [#28](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/28) | `py/empty-except` | `src/adapters/mixins/export_mixin.py:462` | Export invalid polyline area as unknown rather than a fabricated zero. |
| [#29](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/29) | `py/empty-except` | `src/mcp_tools/tools/export.py:211` | Keep the optional observation/presentation fallback and record why it was unavailable. |
| [#31](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/31) | `py/empty-except` | `tests/manual/inspect_entity.py:34` | Keep the optional observation/presentation fallback and record why it was unavailable. |
| [#32](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/32) | `py/empty-except` | `tests/manual/inspect_entity.py:40` | Keep the optional observation/presentation fallback and record why it was unavailable. |
| [#33](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/33) | `py/empty-except` | `tests/manual/inspect_entity.py:65` | Keep the optional observation/presentation fallback and record why it was unavailable. |
| [#34](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/34) | `py/empty-except` | `src/adapters/mixins/layer_mixin.py:199` | Keep best-effort temporary-resource cleanup, with a contextual diagnostic and no masking of a primary failure. |
| [#35](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/35) | `py/empty-except` | `src/adapters/mixins/layer_mixin.py:207` | Keep best-effort temporary-resource cleanup, with a contextual diagnostic and no masking of a primary failure. |
| [#36](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/36) | `py/empty-except` | `src/mcp_tools/tools/layers.py:218` | Keep the optional observation/presentation fallback and record why it was unavailable. |
| [#37](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/37) | `py/empty-except` | `src/adapters/mixins/manipulation_mixin.py:169` | Remove the redundant catch around selection-set cleanup; propagate refused deletion. |
| [#38](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/38) | `py/empty-except` | `src/cad_memory/task_manager.py:259` | Keep the optional observation/presentation fallback and record why it was unavailable. |
| [#40](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/40) | `py/empty-except` | `src/cad_memory/task_manager.py:357` | Report unknown active counts and preserve per-handle observation errors; recover on a later read. |
| [#43](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/43) | `py/empty-except` | `src/adapters/mixins/utility_mixin.py:106` | Allow missing-set lookup; stop manager entry if an existing set cannot be deleted. |
| [#44](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/44) | `py/empty-except` | `src/adapters/mixins/utility_mixin.py:271` | Keep bounded polling retries with diagnostic context; retain the timeout result. |
| [#45](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/45) | `py/empty-except` | `src/adapters/mixins/utility_mixin.py:280` | Allow missing-set lookup; propagate a refused deletion before recreation. |
| [#46](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/46) | `py/empty-except` | `src/cad_memory/verifier.py:360` | Preserve layer-linetype observation errors; required center/hidden comparisons remain fail-closed. |
| [#47](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/47) | `py/inheritance/incorrect-overridden-signature` | `src/cad_memory/database.py:633` | Give the failing store test double the exact production keyword-only signature. |
| [#49](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/49) | `py/import-and-import-from` | `src/adapters/mixins/utility_mixin.py:17` | Use core module aliases consistently, including nested helper paths, while preserving exception/type object identity. |
| [#50](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/50) | `py/pythagorean` | `src/adapters/mixins/drawing_mixin.py:487` | Use math.hypot for a stable planar dimension length. |
| [#51](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/51) | `py/pythagorean` | `src/adapters/mixins/manipulation_mixin.py:471` | Use math.hypot for stable planar path segment lengths. |
| [#126](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/126) | `py/unreachable-statement` | `src/adapters/mixins/export_mixin.py:359` | Remove the constant condition and its unreachable statistics branch. |
| [#127](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/127) | `py/multiple-definition` | `src/adapters/mixins/layer_mixin.py:241` | Remove redundant initial color assignment; all success/fallback branches still assign it. |
| [#128](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/128) | `py/unused-local-variable` | `src/adapters/mixins/drawing_mixin.py:696` | Keep base-point normalization and remove the unused COM variant conversion. |
| [#129](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/129) | `py/unused-local-variable` | `src/adapters/mixins/file_mixin.py:49` | Remove an unused output-directory resolution; actual export path checks remain intact. |
| [#130](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/130) | `py/unused-local-variable` | `src/mcp_tools/tools/session.py:88` | Keep the adapter connection call and discard only its unused local binding. |
| [#131](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/131) | `py/unused-global-variable` | `src/adapters/autocad_adapter.py:37` | Remove an unused module logger. |
| [#136](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/136) | `py/import-and-import-from` | `tests/unit/test_cad_vision_analyzer.py:10` | Use the analyzer module namespace consistently, including monkeypatch targets and calls. |
| [#137](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/137) | `py/import-and-import-from` | `tests/unit/test_cad_vision_ocr.py:8` | Use the OCR module namespace consistently, including monkeypatch targets and calls. |
| [#143](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/143) | `py/empty-except` | `src/mcp_tools/helpers.py:29` | Reconfigure each stream independently so an unsupported stdin does not skip stdout/stderr. |
| [#144](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/144) | `py/empty-except` | `src/cad_memory/task_manager.py:263` | Keep the optional observation/presentation fallback and record why it was unavailable. |
| [#145](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/145) | `py/implicit-string-concatenation-in-list` | `scripts/benchmark_real_drawings.py:349` | Make intended report prose concatenation explicit; preserve output text. |
| [#146](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/146) | `py/implicit-string-concatenation-in-list` | `scripts/benchmark_real_drawings.py:352` | Make intended table header concatenation explicit; preserve column structure. |
| [#147](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/147) | `py/implicit-string-concatenation-in-list` | `scripts/benchmark_real_drawings.py:367` | Make intended production-gate prose concatenation explicit; preserve output text. |
| [#148](https://github.com/linllllll1226-eng/multiCAD-mcp/security/code-scanning/148) | `py/implicit-string-concatenation-in-list` | `scripts/benchmark_real_drawings.py:369` | Make intended overlay legend concatenation explicit; preserve output text. |

## Validation boundaries

Regression tests cover unknown-count recovery, refused selection cleanup, independent document activation, invalid/zero/derived export measurements, stream fallback and preservation of a primary exception. Run the complete Windows suite, unchanged coverage floors, Ruff, mypy, docstrings, strict documentation, build/wheel checks and fresh branch CodeQL before accepting the change. These software checks do not establish a new live-DWG or whole-source reconstruction acceptance.

## Full-analysis follow-up

The full main analysis at `146cd92a390687ff3544b8754a7370be8267d928`, after PR #30, confirmed 52 of the 54 reviewed alerts fixed, including all 37 empty handlers. It retained #6 for additional mixin host-method declarations and #49 for three nested `core` imports. The follow-up explicitly binds all 24 reported host methods to their existing runtime providers and removes those nested imports. It preserves the original function descriptors, exception identities and all 74 type-declaration placeholders. Confirm closure with a fresh full main analysis after this follow-up; a zero-result PR diff analysis alone is insufficient.
