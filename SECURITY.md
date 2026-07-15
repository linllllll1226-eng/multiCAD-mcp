# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 0.4.x | Yes |
| 0.3.x and earlier | No |

Security fixes are applied to the latest release line. Users of older versions should upgrade
before reporting behavior that is already fixed in the current release.

## Reporting a vulnerability

Please do not disclose vulnerabilities, credentials, private DWG content, or machine-specific
paths in a public issue.

Use GitHub's **Security** tab and choose **Report a vulnerability** to submit a private report.
Include the affected version, entry point (`server_memory.py` or legacy `server.py`), reproduction
steps, impact, and a minimal sanitized example. If private vulnerability reporting is not enabled,
open a public issue containing no exploit details and ask the maintainers for a private contact.

You should receive an acknowledgement within seven days. Confirmed issues will be triaged by
severity, fixed on a private branch when appropriate, and disclosed through a GitHub Security
Advisory or release notes after a fix is available.

## Security boundaries

- The supported guarded AutoCAD entry point is `src/server_memory.py` over local STDIO.
- Every enhanced CAD write must pass `cad_plan_validate`, `cad_execute_plan`, and
  `cad_verify_execution` in that order.
- `src/server.py` is retained for upstream compatibility and does not provide the complete guarded
  write contract.
- Local SQLite memory, OCR model caches, generated drawings, and user configuration are not source
  artifacts and must not be committed.
- OCR is evidence extraction only. Missing engineering dimensions must not be inferred as formal
  geometry.
- Never use a real drawing for first-run or integration tests; use a blank unsaved DWG or a copy.

## Maintainer checks

Release candidates must pass the automated tests, Ruff, strict MkDocs build, Bandit, pip-audit,
tool-registration verification, the OCR benchmark, and a blank-DWG guarded workflow acceptance
test. The detailed commands and reviewed exceptions are recorded in
[`docs/SECURITY_AUDIT.md`](docs/SECURITY_AUDIT.md).
