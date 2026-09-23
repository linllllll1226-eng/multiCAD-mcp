"""Enforce per-module floors for safety-critical CAD evidence paths."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

FLOORS = {
    "src/cad_memory/executor.py": 74,
    "src/cad_memory/validator.py": 74,
    "src/cad_memory/verifier.py": 83,
    "src/cad_memory/task_manager.py": 90,
    "src/cad_memory/receipts.py": 88,
    "src/cad_memory/acceptance.py": 90,
    "src/cad_memory/dimension_points.py": 100,
    "src/cad_vision/audit_renderer.py": 79,
    "src/cad_vision/analyzer.py": 90,
    "src/cad_vision/dimensions.py": 95,
    "src/cad_vision/pdf.py": 89,
    "src/cad_vision/ocr.py": 89,
}


def failures(report: object) -> list[str]:
    """Reject missing modules, empty measurements and any floor regression."""
    if not isinstance(report, dict) or not isinstance(report.get("files"), dict):
        return ["Invalid coverage report: expected a files mapping"]
    if any(not isinstance(name, str) for name in report["files"]):
        return ["Invalid coverage report: file paths must be strings"]
    files = {name.replace("\\", "/"): row for name, row in report["files"].items()}
    errors = []
    for name, floor in FLOORS.items():
        row = files.get(name)
        if row is None:
            errors.append(f"{name}: coverage is missing")
            continue
        summary = row.get("summary") if isinstance(row, dict) else None
        if not isinstance(summary, dict):
            errors.append(f"{name}: invalid or missing summary")
            continue
        statements = summary.get("num_statements")
        covered = summary.get("covered_lines")
        if type(statements) is not int or statements <= 0:
            errors.append(f"{name}: no executable statements measured")
            continue
        if type(covered) is not int or not 0 <= covered <= statements:
            errors.append(f"{name}: invalid covered line count")
            continue
        reported = summary.get("percent_covered")
        if type(reported) not in (int, float) or not math.isfinite(reported):
            errors.append(f"{name}: invalid coverage percentage")
            continue
        # Derive line coverage from counts: rounded or inflated summary values
        # must never hide a regression at the threshold.
        percent = 100 * covered / statements
        if not 0 <= reported <= 100:
            errors.append(f"{name}: invalid coverage percentage")
        elif percent < floor:
            errors.append(f"{name}: {percent:.2f}% < {floor}%")
    return errors


def main() -> int:
    """Read pytest-cov JSON and emit actionable CI failures."""
    parser = argparse.ArgumentParser()
    parser.add_argument("report", nargs="?", default="coverage.json")
    args = parser.parse_args()
    try:
        errors = failures(json.loads(Path(args.report).read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        errors = [f"Unable to read coverage report: {exc}"]
    print("\n".join(errors) if errors else f"Coverage gates passed: {len(FLOORS)} critical modules")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
