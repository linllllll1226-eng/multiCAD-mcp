# Quality gates

The authoritative typing configuration is `[tool.mypy]` in pyproject.toml.
The duplicate mypy.ini is removed. The baseline is **zero project errors**.
COM dispatch and untyped optional libraries use narrow import overrides;
project modules remain checked. Defensive runtime checks are retained
(`warn_unreachable=false`, matching the formerly authoritative configuration).

Run the same commands used by Windows CI:

```powershell
uv sync --frozen --extra dev --extra vision --extra docs
uv run pytest -q -p no:cacheprovider --cov=src --cov-report=json --cov-report=xml
uv run python scripts/check_coverage_gates.py
uv run mypy src
uv run interrogate src
uv run ruff check .
uv run ruff format --check .
uv run mkdocs build --strict
uv run python scripts/check_repository_hygiene.py
```

Docstring coverage must remain at least 80%. The coverage checker independently
enforces 12 safety-critical module floors: executor/validator 74%, verifier 83%,
task manager/acceptance 90%, receipts 88%, dimension-point reading 100%,
audit renderer 79%, analyzer 90%, typed dimensions 95%, PDF/OCR 89%.
Line coverage is computed from covered/executable line counts; an inflated or
rounded percentage cannot hide a regression. Malformed, empty and non-finite
measurements fail with actionable diagnostics.
These floors are rounded below the measured baseline rather than claiming
unexecuted COM/UI paths are covered. Missing modules or empty measurements fail.
CI uploads the full XML/JSON report for review. Increase floors as tests are added;
do not lower them to make a regression pass. Cloud tests are not live-DWG acceptance.
