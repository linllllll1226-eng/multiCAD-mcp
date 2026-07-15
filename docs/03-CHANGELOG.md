# Changelog

All notable changes to multiCAD-mcp will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-07-15

### Added

- Optional local PaddleOCR 3.x integration for scanned images and image-only PDFs.
- OCR text boxes, confidence values, page metadata, and engineering dimension candidates.
- ASCII-safe, user-overridable Paddle model cache under `data/paddle_models`.
- Deterministic OCR benchmark and provider-failure tests.
- Repository hygiene checker that rejects tracked runtime data and dirty release worktrees.

### Changed

- Refreshed the README and vision documentation to describe the current guarded workflow.
- Upgraded CI from partial linting to full Ruff checks and formatting validation.
- Adopted a 100-character line limit and Google-style docstring convention across the codebase.

### Fixed

- Eliminated 728 pre-existing Ruff violations without changing the guarded write contract.
- Avoided Paddle inference failures caused by non-ASCII Windows profile paths.

### Security

- OCR remains read-only and local; it never connects to or writes AutoCAD.
- Vector PDF text remains preferred, and uncertain OCR evidence still requires guarded planning.

## [0.3.0] - 2026-07-15

### Added

- **Guarded CAD workflow**: structured plan validation, controlled execution, and verification against real AutoCAD entity data.
- **Local learning layer**: user-confirmed corrections, drawing profiles, and execution results stored in a Git-ignored SQLite database.
- **Task lifecycle**: persistent `task_id` metadata, entity provenance, verification-gated preview commit, and task-scoped revert without global `UNDO`.
- **Dimension safeguards**: native diameter/radius dimensions, empty `TextOverride`, fill-state verification, and geometry-preserving annotation moves.
- **Vision assistance**: optional PDF/raster preprocessing, CAD evidence extraction, cache, capability reporting, and deterministic benchmarks.
- **Usability layer**: one-click launcher, profile synchronization, write-before-backup helper, and guarded template initializer.
- **Expanded MCP surface**: 23 tools in the enhanced entry point (7 upstream unified tools plus 16 enhanced tools).
- **Windows CI**: Python 3.10-3.12 tests, critical Ruff checks, focused vision lint checks, and strict documentation builds.

### Changed

- Enhanced Codex deployments now use `src/server_memory.py`; `src/server.py` remains available for upstream compatibility.
- Package version advanced to `0.3.0` and documentation was reorganized around the guarded AutoCAD workflow.
- AutoCAD 2022 is explicitly documented as the accepted target for enhanced COM behavior.

### Fixed

- Corrected AutoCAD 2D polyline coordinate verification.
- Hardened Codex Desktop startup selection and stale npm-shim fallback behavior.
- Removed a duplicate spline mapping and corrected boolean type comparisons found during release linting.

### Security

- Enhanced writes fail closed when validation, execution, or verification is unavailable.
- Approximate geometry cannot enter formal outline layers.
- Commit and revert operations are scoped to verified AI task metadata.
- Local memory and vision cache files remain excluded from version control.

### Known limitations

- The guarded executor intentionally does not support every arbitrary edit or delete operation.
- Vision preprocessing is evidence assistance, not a substitute for missing engineering dimensions or human visual review.
- Enhanced COM acceptance testing is currently specific to AutoCAD 2022.

---

## [0.2.1] - 2026-06-27

### Added

- **Table Entity Support**: Added support for drawing native table entities using the `table` command in `draw_entities` (shorthand alias `tab`).
- **Bypass path traversal restrictions**: Added `allow_arbitrary_paths` configuration parameter in `output` settings. This allows users to save drawings to any absolute path on their host system, bypassing standard path traversal protection if explicitly enabled.
- **Selection mapping for tables**: Registered `table` to map to `AcDbTable` in entity selections.

### Fixed

- **Type Checking Warning**: Fixed a type-checking error in `DrawMLeaderRequest` initialization where `text_height` could resolve to `Any | None` instead of a non-nullable `float`.

---

## [0.2.0] - 2026-03-14

### Security (CRITICAL)

- **Path Traversal Prevention**: Added `_validate_export_path()` to prevent directory traversal attacks in file export operations.
- **Command Injection Mitigation**: Added `_sanitize_command_input()` to sanitize CAD command inputs, preventing malicious command injection.
- **Thread-Safe Singletons**: Implemented double-checked locking pattern in `AdapterRegistry` and `ConfigManager` for thread-safe operation.
- **COM Initialization Safety**: Improved error handling in `connection_mixin.py` for COM initialization across threads.

### Added

- **Block attribute management**: `get_attrs` and `set_attrs` actions in `manage_blocks` — read and write attribute tag values on block references.
- **Modern packaging**: `pyproject.toml` with full project metadata, dev/docs dependency groups, `[tool.ruff]`, `[tool.mypy]`, `[tool.interrogate]`, and `[tool.pytest]` configuration.
- **MkDocs documentation site**: Material theme with auto-generated API reference via mkdocstrings.

### Changed

- **Unified tool architecture**: 55 specific CAD commands replaced by 7 unified dispatch tools using compact shorthand format (~85% token reduction).
  - `manage_session` (11 actions), `draw_entities` (10 types), `manage_blocks` (6 actions), `manage_layers` (9 actions), `manage_files` (5 actions), `manage_entities` (10 actions), `export_data` (4 combinations).
- **Auto-named exports**: Excel export defaults to `[drawing_name]_data.xlsx` instead of `drawing_data.xlsx`.
- **Excel improvements**: autofilter enabled on all sheets (Entities, Layers, Blocks); `limit=0` ensures full export.
- **Dashboard refactor**: removed background refresher thread; export and refresh run directly on MCP thread; centralized configuration in `config.json` (port 8888).
- **Test suite**: expanded from 62 to 171 tests.

### Performance

- **O(n*m) → O(1) Optimization**: Optimized entity lookup in `set_entities_color_bylayer()` using `HandleToObject()` API.
  - Replaced inefficient nested loop iteration with direct handle-to-object lookups.
  - Expected 60%+ improvement on drawings with 10,000+ entities.

### Bug Fixes

- **Missing Return Statement**: Fixed `_paste()` function missing return value in `entities.py`.
- **Hardcoded Version**: Updated `web/api.py` to import version from `__version__.py` instead of hardcoding.
- **JSON Error Handling**: Added JSON error handling in `_set_color_bylayer()` with proper error messages.
- **Coordinate Validation**: Improved coordinate parsing with better error messages in paste operations.

---

## [0.1.3] - 2026-02-12

### Changed - Mixin Architecture Refactor

Major refactoring of the adapter layer for better maintainability.

#### Architecture

- **Mixin-based adapter**: `autocad_adapter.py` reduced from 3,198 to 99 lines.
- **11 specialized mixins**: Each mixin handles a specific responsibility (Utility, Connection, Drawing, Layer, File, View, Selection, Entity, Manipulation, Block, Export).
- **AdapterRegistry**: Encapsulated global state in singleton class.
- **Removed NLP**: Natural language processor removed (use direct tool calls).

#### Bug Fixes

- Fixed `@staticmethod` error in `validate_lineweight`.

#### Improvements

- **Refactored `DrawingMixin`**: Reduces boilerplate code in drawing methods using `_finalize_entity` helper.
- **Simplified `draw_mleader`**: Extracted complex fallback logic to improve readability.
- **Documentation**: Simplified `README.md` and updated documentation structure.

---

## [0.1.2] - 2025-12-09

### Added

- **Block creation**: `create_block` tool (from handles or selection).
- Core methods: `create_block_from_entities()`, `create_block_from_selection()`.
- 7 new tests (42 total).

### Changed

- Direct instantiation: `AutoCADAdapter(cad_type)` replaces factory.
- Context managers: `com_session()`, `SelectionSetManager`.
- Performance: `PickfirstSelectionSet` for fast entity access.

---

## [0.1.1] - 2025-11-22

### Added - Batch Operations

**13 batch operation tools** (legacy tools replaced by current unified architecture in 0.2.0):
- Drawing: `draw_lines`, `draw_circles`, `draw_arcs`, `draw_rectangles`, `draw_polylines`, `draw_texts`, `add_dimensions`.
- Layers: `rename_layers`, `delete_layers`, `turn_layers_on`, `turn_layers_off`.
- Entities: `change_entities_colors`, `change_entities_layers`.

---

## [0.1.0] - 2025-11-12

### Initial Release

- **Multi-CAD support**: AutoCAD, ZWCAD, GstarCAD, BricsCAD.
- **FastMCP 2.0** server with MCP tools.
- **Universal adapter** via COM API.
- **Excel export** with locale support.
- **Type safety**: 100% type hints.
- **Testing**: Comprehensive test suite.
