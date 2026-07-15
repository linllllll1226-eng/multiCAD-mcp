"""Verify additive task-table migration on a disposable cad_memory.db copy."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cad_memory.database import SQLiteMemoryStore  # noqa: E402

LEGACY_TABLES = ("corrections", "drawing_profiles", "execution_results")
TASK_TABLES = {"ai_tasks", "ai_task_entities"}


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "database",
        type=Path,
        help="Disposable database copy. Never point this helper at the live database.",
    )
    return parser.parse_args()


def _snapshot(path: Path) -> tuple[set[str], dict[str, int]]:
    with sqlite3.connect(path) as connection:
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        counts = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in LEGACY_TABLES
        }
    return tables, counts


def main() -> int:
    """Run the additive migration check and print a JSON report."""
    args = _arguments()
    path = args.database.resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    if path.name.casefold() == "cad_memory.db":
        raise ValueError("Refusing a live-looking cad_memory.db name; use a disposable copy name")
    before_tables, before_counts = _snapshot(path)
    SQLiteMemoryStore(path)
    after_tables, after_counts = _snapshot(path)
    report = {
        "database_copy": str(path),
        "legacy_counts_unchanged": before_counts == after_counts,
        "task_tables_added": TASK_TABLES <= after_tables,
        "before_tables": sorted(before_tables),
        "after_tables": sorted(after_tables),
    }
    report["passed"] = bool(report["legacy_counts_unchanged"] and report["task_tables_added"])
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
