"""Exercise coverage failures that must block a release."""

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/check_coverage_gates.py"
SPEC = importlib.util.spec_from_file_location("coverage_gates", SCRIPT)
GATES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATES)


def report_at_floors():
    return {
        "files": {
            name: {
                "summary": {"num_statements": 100, "covered_lines": floor, "percent_covered": floor}
            }
            for name, floor in GATES.FLOORS.items()
        }
    }


def test_exact_floors_and_windows_paths_pass():
    report = report_at_floors()
    assert GATES.failures(report) == []
    report["files"] = {name.replace("/", "\\"): row for name, row in report["files"].items()}
    assert GATES.failures(report) == []


def test_unrelated_high_coverage_cannot_hide_a_critical_regression():
    report = report_at_floors()
    name = next(iter(GATES.FLOORS))
    report["files"][name]["summary"]["covered_lines"] -= 1
    report["files"]["unrelated.py"] = {"summary": {"percent_covered": 100}}
    errors = GATES.failures(report)
    assert len(errors) == 1 and name in errors[0] and "<" in errors[0]


def test_missing_critical_module_fails():
    report = report_at_floors()
    name = next(iter(GATES.FLOORS))
    del report["files"][name]
    assert GATES.failures(report) == [f"{name}: coverage is missing"]


@pytest.mark.parametrize("report", [None, [], {}, {"files": None}, {"files": {1: {}}}])
def test_malformed_top_level_reports_fail(report):
    assert GATES.failures(report)


@pytest.mark.parametrize(
    "row",
    [
        None,
        {},
        {"summary": None},
        {"summary": {"num_statements": 0}},
        {"summary": {"num_statements": True}},
        {"summary": {"num_statements": 100, "covered_lines": 101, "percent_covered": 100}},
        {"summary": {"num_statements": 100, "covered_lines": -1, "percent_covered": 100}},
        {"summary": {"num_statements": 100, "covered_lines": 100, "percent_covered": float("nan")}},
        {"summary": {"num_statements": 100, "covered_lines": 100, "percent_covered": float("inf")}},
        {"summary": {"num_statements": 100, "covered_lines": 100, "percent_covered": 101}},
    ],
)
def test_malformed_measurements_fail(row):
    report = report_at_floors()
    report["files"][next(iter(GATES.FLOORS))] = copy.deepcopy(row)
    assert GATES.failures(report)


def test_cli_bad_json_returns_failure_without_traceback(tmp_path):
    path = tmp_path / "coverage.json"
    path.write_text("{broken", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True
    )
    assert result.returncode == 1
    assert "Unable to read coverage report" in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_empty_report_fails(tmp_path):
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps({"files": {}}), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True
    )
    assert result.returncode == 1
    assert "coverage is missing" in result.stdout
