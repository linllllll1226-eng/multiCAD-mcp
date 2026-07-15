# Documentation Index

## v0.3 at a glance

- 23 MCP tools: 7 upstream unified tools plus 16 enhanced workflow tools.
- 245 automated tests at the v0.3 integration point.
- Python 3.10+, FastMCP 3.1+, Windows COM.
- AutoCAD 2022 is the verified target for guarded execution, task tracking, and native dimension checks.

## Core documentation

| Document | Purpose |
|---|---|
| [01-SETUP.md](01-SETUP.md) | Installation and basic setup |
| [02-ARCHITECTURE.md](02-ARCHITECTURE.md) | Upstream architecture and extension guide |
| [05-REFERENCE.md](05-REFERENCE.md) | Unified MCP tool reference |
| [04-TROUBLESHOOTING.md](04-TROUBLESHOOTING.md) | Debugging and log analysis |
| [06-WEB-DASHBOARD.md](06-WEB-DASHBOARD.md) | Optional dashboard and API |
| [03-CHANGELOG.md](03-CHANGELOG.md) | Version history |

## Enhanced AutoCAD workflow

| Document | Purpose |
|---|---|
| [CAD_MEMORY_VALIDATION.md](CAD_MEMORY_VALIDATION.md) | Local corrections, structured plans, guarded execution, and verification |
| [CAD_TASK_TRACKING.md](CAD_TASK_TRACKING.md) | `task_id`, entity provenance, safe commit, and task-scoped revert |
| [CAD_SAFETY_HARDENING.md](CAD_SAFETY_HARDENING.md) | Strict write policy, receipts, and rollback boundaries |
| [CAD_UX_IMPROVEMENTS.md](CAD_UX_IMPROVEMENTS.md) | Launcher, profiles, backups, and template preparation |
| [CAD_VISION_PIPELINE.md](CAD_VISION_PIPELINE.md) | Optional PDF/image preprocessing and evidence extraction |
| [CAD_VISION_BENCHMARK.md](CAD_VISION_BENCHMARK.md) | Deterministic accuracy and efficiency benchmark methodology |

For enhanced writes, always use:

```text
cad_plan_validate -> cad_execute_plan -> cad_verify_execution
```
