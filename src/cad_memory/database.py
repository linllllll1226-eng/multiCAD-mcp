"""SQLite-backed local experience memory for CAD tasks."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

DEFAULT_DATABASE_PATH = Path(__file__).resolve().parents[2] / "data" / "cad_memory.db"
ALLOWED_TABLES = {"corrections", "drawing_profiles", "execution_results"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _decode_json_fields(row: dict[str, Any]) -> dict[str, Any]:
    for key in (
        "context",
        "layer_rules",
        "dimension_rules",
        "planned_data",
        "actual_data",
        "errors",
    ):
        value = row.get(key)
        if isinstance(value, str) and value:
            try:
                row[key] = json.loads(value)
            except json.JSONDecodeError:
                pass
    if "confirmed_by_user" in row:
        row["confirmed_by_user"] = bool(row["confirmed_by_user"])
        row["enforceable"] = row["confirmed_by_user"]
    if "passed" in row:
        row["passed"] = bool(row["passed"])
    return row


class SQLiteMemoryStore:
    """Manage the local SQLite CAD memory database."""

    def __init__(self, path: str | Path = DEFAULT_DATABASE_PATH) -> None:
        """Initialize the store and create its schema when needed."""
        self.path = Path(path).expanduser().resolve()
        self._lock = threading.RLock()
        self.initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create the database and required tables if they do not exist."""
        with self._lock, self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS corrections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,
                    trigger TEXT NOT NULL,
                    wrong_behavior TEXT NOT NULL,
                    correct_behavior TEXT NOT NULL,
                    context TEXT NOT NULL DEFAULT '{}',
                    confirmed_by_user INTEGER NOT NULL DEFAULT 0
                        CHECK (confirmed_by_user IN (0, 1)),
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_corrections_category
                    ON corrections(category);
                CREATE INDEX IF NOT EXISTS idx_corrections_confirmed
                    ON corrections(confirmed_by_user);

                CREATE TABLE IF NOT EXISTS drawing_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    unit TEXT NOT NULL,
                    layer_rules TEXT NOT NULL DEFAULT '{}',
                    dimension_rules TEXT NOT NULL DEFAULT '{}',
                    notes TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS execution_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_name TEXT NOT NULL,
                    planned_data TEXT NOT NULL,
                    actual_data TEXT NOT NULL,
                    passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
                    errors TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                );
                """
            )

    def add_correction(
        self,
        *,
        category: str,
        trigger: str,
        wrong_behavior: str,
        correct_behavior: str,
        context: Any | None = None,
        confirmed_by_user: bool = False,
    ) -> dict[str, Any]:
        """Add a correction; only user-confirmed rows are enforceable."""
        values = [category, trigger, wrong_behavior, correct_behavior]
        if any(not str(value).strip() for value in values):
            raise ValueError(
                "category, trigger, wrong_behavior and correct_behavior are required"
            )
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO corrections (
                    category, trigger, wrong_behavior, correct_behavior,
                    context, confirmed_by_user, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    category.strip(),
                    trigger.strip(),
                    wrong_behavior.strip(),
                    correct_behavior.strip(),
                    _json(context or {}),
                    int(bool(confirmed_by_user)),
                    _utc_now(),
                ),
            )
            record_id = int(cursor.lastrowid)
        return self.get_record("corrections", record_id)

    def search_corrections(
        self,
        query: str,
        *,
        category: str | None = None,
        include_unconfirmed: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Search corrections, excluding unconfirmed rows by default."""
        limit = max(1, min(int(limit), 500))
        clauses = []
        params: list[Any] = []
        if query.strip():
            token = f"%{query.strip()}%"
            clauses.append(
                "(trigger LIKE ? OR wrong_behavior LIKE ? "
                "OR correct_behavior LIKE ? OR context LIKE ?)"
            )
            params.extend([token, token, token, token])
        if category:
            clauses.append("category = ?")
            params.append(category)
        if not include_unconfirmed:
            clauses.append("confirmed_by_user = 1")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM corrections{where} ORDER BY id DESC LIMIT ?", params
            ).fetchall()
        return [_decode_json_fields(dict(row)) for row in rows]

    def list_records(
        self, table: str, *, limit: int = 100, include_unconfirmed: bool = False
    ) -> list[dict[str, Any]]:
        """List records from one allowed table."""
        table = self._validate_table(table)
        limit = max(1, min(int(limit), 500))
        where = (
            " WHERE confirmed_by_user = 1"
            if table == "corrections" and not include_unconfirmed
            else ""
        )
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM {table}{where} ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_decode_json_fields(dict(row)) for row in rows]

    def get_record(self, table: str, record_id: int) -> dict[str, Any]:
        """Get one record by table and integer ID."""
        table = self._validate_table(table)
        with self._lock, self._connection() as connection:
            row = connection.execute(
                f"SELECT * FROM {table} WHERE id = ?", (int(record_id),)
            ).fetchone()
        if row is None:
            raise KeyError(f"No {table} record with id={record_id}")
        return _decode_json_fields(dict(row))

    def delete_record(self, table: str, record_id: int, *, confirmed: bool) -> bool:
        """Delete a record only after explicit confirmation."""
        table = self._validate_table(table)
        if not confirmed:
            raise PermissionError("Deletion requires confirmed=true")
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                f"DELETE FROM {table} WHERE id = ?", (int(record_id),)
            )
            return cursor.rowcount == 1

    def save_drawing_profile(
        self,
        *,
        name: str,
        unit: str,
        layer_rules: Any,
        dimension_rules: Any,
        notes: str = "",
    ) -> dict[str, Any]:
        """Create or update a named drawing profile."""
        if not name.strip() or not unit.strip():
            raise ValueError("name and unit are required")
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO drawing_profiles
                    (name, unit, layer_rules, dimension_rules, notes)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    unit=excluded.unit,
                    layer_rules=excluded.layer_rules,
                    dimension_rules=excluded.dimension_rules,
                    notes=excluded.notes
                """,
                (
                    name.strip(),
                    unit.strip(),
                    _json(layer_rules),
                    _json(dimension_rules),
                    notes,
                ),
            )
            row = connection.execute(
                "SELECT * FROM drawing_profiles WHERE name = ?", (name.strip(),)
            ).fetchone()
        return _decode_json_fields(dict(row))

    def load_drawing_profile(self, name: str) -> dict[str, Any]:
        """Load one drawing profile by name."""
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM drawing_profiles WHERE name = ?", (name.strip(),)
            ).fetchone()
        if row is None:
            raise KeyError(f"Drawing profile not found: {name}")
        return _decode_json_fields(dict(row))

    def record_execution(
        self,
        *,
        task_name: str,
        planned_data: Any,
        actual_data: Any,
        passed: bool,
        errors: Any,
    ) -> dict[str, Any]:
        """Persist a plan execution or verification result."""
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO execution_results
                    (task_name, planned_data, actual_data, passed, errors, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    task_name,
                    _json(planned_data),
                    _json(actual_data),
                    int(bool(passed)),
                    _json(errors),
                    _utc_now(),
                ),
            )
            record_id = int(cursor.lastrowid)
        return self.get_record("execution_results", record_id)

    @staticmethod
    def _validate_table(table: str) -> str:
        if table not in ALLOWED_TABLES:
            raise ValueError(f"table must be one of {sorted(ALLOWED_TABLES)}")
        return table
