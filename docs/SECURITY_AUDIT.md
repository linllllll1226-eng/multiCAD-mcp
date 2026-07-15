# Security audit

This page records the v0.4.0 release security gate. It is not a claim that the software is free of
all vulnerabilities; it documents the reviewed scope, reproducible checks, and known boundaries.

## Release gate

Run these commands from the repository root in PowerShell:

```powershell
uv sync --frozen --extra dev --extra docs --extra vision --extra ocr
uv run pytest -q -p no:cacheprovider
uv run ruff check .
uv run ruff format --check .
uv run mkdocs build --strict
uv run bandit -r src -ll -ii
$auditFile = Join-Path $env:TEMP "multicad-audit-requirements.txt"
uv export --frozen --all-extras --all-groups --no-emit-project `
  --no-hashes --format requirements-txt --output-file $auditFile
uv run pip-audit --requirement $auditFile --no-deps --strict --progress-spinner off
uv run python scripts/check_repository_hygiene.py
uv run python scripts/benchmark_cad_ocr.py
```

The MCP registration check must also import `src/server_memory.py` in an isolated process and
confirm exactly 23 registered tools. AutoCAD acceptance is performed separately in a blank unsaved
DWG with the guarded three-stage workflow; it is intentionally not part of cloud CI.

## Dependency remediation

The v0.4.0 lock refresh upgrades the complete dependency graph and raises minimum supported
versions for the primary security-sensitive runtime packages. The refreshed environment includes
patched FastMCP/MCP, FastAPI/Starlette, Pillow, Pydantic Settings, Cryptography, Requests, urllib3,
PyJWT, python-multipart, Pygments, and pytest families. `pip-audit` is required to report zero known
vulnerabilities before release unless a specific advisory, exploitability analysis, owner, and
expiry date are documented here.

There are currently no accepted vulnerability exceptions for v0.4.0.

## v0.4.0 results

The local Windows release gate on 2026-07-15 produced these results:

| Check | Result |
|---|---|
| Automated tests | 252 passed in 5.10 seconds on the final warm rerun |
| Ruff lint | Passed, zero findings |
| Ruff format | 95 files formatted and check passed |
| MkDocs | Strict build passed |
| Enhanced MCP registration | Exactly 23 tools |
| Bandit (`-ll -ii`) | Zero unsuppressed medium/high findings |
| Full Bandit scan (informational) | 33 low-severity findings: B101 x1, B110 x30, B112 x2 |
| pip-audit | Zero known vulnerabilities across the complete exported lock set |
| OCR real benchmark | 5 text regions, 4 dimension kinds, 100% expected-kind recall |
| OCR cold request | 22,019.011 ms with local cached models |
| OCR warm result cache | 1.589 ms, cache hit |

Repository hygiene is rerun after the release commit because the checker intentionally rejects a
dirty worktree.

The AutoCAD 2022 blank-DWG acceptance also passed:

- active document: `Drawing1.dwg`, unsaved, `INSUNITS=4` (`mm`);
- entity count before execution: 0;
- workflow: `cad_plan_validate` -> `cad_execute_plan` -> `cad_verify_execution`;
- created object: one `AcDbCircle` on `AI_PREVIEW_OUTLINE`, center `(0, 0, 0)`, radius 25;
- verified diameter: 50, error 0;
- entity count after execution: 1;
- task provenance: `cad_20260715T155934Z_9cd6c40d`;
- the acceptance drawing was not saved or closed.

## Reviewed Bandit B608 findings

Bandit originally reported 10 medium-confidence B608 warnings in
`src/cad_memory/database.py`. They were reviewed as constrained dynamic SQL rather than
user-controlled SQL:

- table identifiers pass `_validate_table()` and are limited to `ALLOWED_TABLES`;
- optional `WHERE`, pagination, and selected-column fragments come from fixed literals;
- dynamic update column lists contain only hard-coded column names;
- `IN` placeholders contain only generated `?` markers;
- every user-provided value remains a SQLite bound parameter.

Refactoring separates query construction from execution, and each of the 10 reviewed constructions
has a narrow `# nosec B608` annotation on the dynamic fragment beside its documented invariant.
Unit tests also verify that table-name injection is rejected and search values cannot alter the
schema. Any new dynamic SQL must either avoid string construction or document and test an
equivalent whitelist and parameter-binding invariant.

## Accepted low-severity Bandit debt

The unfiltered informational Bandit scan reports 33 low-severity findings: one B101, 30 B110, and
two B112 findings. These are tracked code-quality debt for v0.4.0, primarily intentional handling
of optional AutoCAD COM cleanup, property, and update failures, plus one internal assertion. They
are not the 10 reviewed SQL B608 findings and are not dependency-vulnerability exceptions.

Future hardening should replace broad exception suppression with narrower exception types and
debug logging where AutoCAD product compatibility permits. Release gating still fails on every
unsuppressed medium- or high-severity Bandit finding.

## Operational boundaries

- `server_memory.py` is the supported guarded entry point. The legacy server is not equivalent.
- The MCP server uses local STDIO and does not need a listening network socket.
- Blank-DWG acceptance validates real AutoCAD COM behavior but cannot run on GitHub-hosted runners.
- OCR model downloads are external supply-chain inputs cached outside Git; the OCR result is
  evidence and never authorizes a CAD write by itself.
- CodeQL, Dependabot, Bandit, and pip-audit are complementary; none replaces review of CAD write
  authorization, COM behavior, or engineering correctness.

## GitHub controls

- `.github/dependabot.yml` monitors uv and GitHub Actions dependencies weekly.
- `.github/workflows/security.yml` runs Bandit and pip-audit on pushes, pull requests, and weekly.
- `.github/workflows/codeql.yml` runs Python CodeQL security-and-quality queries.
- `SECURITY.md` defines supported versions and coordinated disclosure guidance.
