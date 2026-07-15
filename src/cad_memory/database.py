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
TASK_STATUSES = {
    "executing",
    "executed",
    "verified",
    "committed",
    "reverted",
    "failed",
}


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
        "plan_data",
        "verification_data",
        "metadata",
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
    if "approximate_reference" in row:
        row["approximate_reference"] = bool(row["approximate_reference"])
    if "owned" in row:
        row["owned"] = bool(row["owned"])
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

                CREATE TABLE IF NOT EXISTS ai_tasks (
                    task_id TEXT PRIMARY KEY,
                    task_name TEXT NOT NULL,
                    drawing_name TEXT NOT NULL DEFAULT '',
                    drawing_full_name TEXT NOT NULL DEFAULT '',
                    drawing_profile TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    execution_result_id INTEGER,
                    plan_data TEXT NOT NULL,
                    verification_data TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_ai_tasks_status
                    ON ai_tasks(status);

                CREATE TABLE IF NOT EXISTS ai_task_entities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    handle TEXT NOT NULL,
                    object_type TEXT NOT NULL DEFAULT '',
                    operation TEXT NOT NULL DEFAULT 'create',
                    owned INTEGER NOT NULL DEFAULT 1 CHECK (owned IN (0, 1)),
                    preview_layer TEXT NOT NULL DEFAULT '',
                    current_layer TEXT NOT NULL DEFAULT '',
                    formal_layer TEXT NOT NULL DEFAULT '',
                    source_type TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0,
                    approximate_reference INTEGER NOT NULL DEFAULT 0
                        CHECK (approximate_reference IN (0, 1)),
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    UNIQUE(task_id, handle),
                    FOREIGN KEY(task_id) REFERENCES ai_tasks(task_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_ai_task_entities_task
                    ON ai_task_entities(task_id);
                CREATE INDEX IF NOT EXISTS idx_ai_task_entities_handle
                    ON ai_task_entities(handle);
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
            raise ValueError("category, trigger, wrong_behavior and correct_behavior are required")
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
            cursor = connection.execute(f"DELETE FROM {table} WHERE id = ?", (int(record_id),))
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

    def update_execution_result(
        self,
        record_id: int,
        *,
        actual_data: Any,
        passed: bool,
        errors: Any,
    ) -> dict[str, Any]:
        """Update a pending execution result after CAD execution completes."""
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE execution_results
                SET actual_data = ?, passed = ?, errors = ?
                WHERE id = ?
                """,
                (_json(actual_data), int(bool(passed)), _json(errors), int(record_id)),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"No execution_results record with id={record_id}")
        return self.get_record("execution_results", record_id)

    def create_ai_task(
        self,
        *,
        task_id: str,
        task_name: str,
        drawing_name: str,
        drawing_full_name: str,
        drawing_profile: str,
        status: str,
        execution_result_id: int | None,
        plan_data: Any,
    ) -> dict[str, Any]:
        """Create the durable task record before any CAD object is written."""
        self._validate_task_status(status)
        now = _utc_now()
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO ai_tasks (
                    task_id, task_name, drawing_name, drawing_full_name,
                    drawing_profile, status, execution_result_id, plan_data,
                    verification_data, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)
                """,
                (
                    task_id,
                    task_name,
                    drawing_name,
                    drawing_full_name,
                    drawing_profile,
                    status,
                    execution_result_id,
                    _json(plan_data),
                    now,
                    now,
                ),
            )
        return self.get_ai_task(task_id)

    def update_ai_task(
        self,
        task_id: str,
        *,
        status: str,
        verification_data: Any | None = None,
        execution_result_id: int | None = None,
    ) -> dict[str, Any]:
        """Move a task through an explicit lifecycle state."""
        self._validate_task_status(status)
        assignments = ["status = ?", "updated_at = ?"]
        params: list[Any] = [status, _utc_now()]
        if verification_data is not None:
            assignments.append("verification_data = ?")
            params.append(_json(verification_data))
        if execution_result_id is not None:
            assignments.append("execution_result_id = ?")
            params.append(int(execution_result_id))
        params.append(task_id)
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                f"UPDATE ai_tasks SET {', '.join(assignments)} WHERE task_id = ?",
                params,
            )
            if cursor.rowcount != 1:
                raise KeyError(f"AI task not found: {task_id}")
        return self.get_ai_task(task_id)

    def get_ai_task(self, task_id: str, *, include_entities: bool = True) -> dict[str, Any]:
        """Return one task and optionally its recorded entity rows."""
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM ai_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"AI task not found: {task_id}")
        task = _decode_json_fields(dict(row))
        if include_entities:
            task["entities"] = self.get_ai_task_entities(task_id)
        return task

    def list_ai_tasks(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
        include_details: bool = False,
    ) -> list[dict[str, Any]]:
        """List durable AI tasks with optional heavy plan/verification payloads."""
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        params: list[Any] = []
        where = ""
        if status is not None:
            self._validate_task_status(status)
            where = " WHERE status = ?"
            params.append(status)
        params.extend([limit, offset])
        columns = (
            "*"
            if include_details
            else """
            task_id, task_name, drawing_name, drawing_full_name,
            drawing_profile, status, execution_result_id, created_at, updated_at
        """
        )
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT {columns} FROM ai_tasks{where}
                ORDER BY created_at DESC LIMIT ? OFFSET ?
                """,
                params,
            ).fetchall()
        return [_decode_json_fields(dict(row)) for row in rows]

    def add_ai_task_entities(
        self, task_id: str, entities: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Persist the exact handles and provenance written by one task."""
        now = _utc_now()
        with self._lock, self._connection() as connection:
            for entity in entities:
                connection.execute(
                    """
                    INSERT INTO ai_task_entities (
                        task_id, handle, object_type, operation, owned,
                        preview_layer, current_layer, formal_layer, source_type,
                        confidence, approximate_reference, metadata, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_id, handle) DO UPDATE SET
                        object_type=excluded.object_type,
                        operation=excluded.operation,
                        owned=excluded.owned,
                        preview_layer=excluded.preview_layer,
                        current_layer=excluded.current_layer,
                        formal_layer=excluded.formal_layer,
                        source_type=excluded.source_type,
                        confidence=excluded.confidence,
                        approximate_reference=excluded.approximate_reference,
                        metadata=excluded.metadata
                    """,
                    (
                        task_id,
                        entity["handle"],
                        entity.get("object_type", ""),
                        entity.get("operation", "create"),
                        int(bool(entity.get("owned", True))),
                        entity.get("preview_layer", ""),
                        entity.get("current_layer", entity.get("preview_layer", "")),
                        entity.get("formal_layer", ""),
                        entity.get("source_type", ""),
                        float(entity.get("confidence", 0)),
                        int(bool(entity.get("approximate_reference", False))),
                        _json(entity.get("metadata", {})),
                        now,
                    ),
                )
        return self.get_ai_task_entities(task_id)

    def get_ai_task_entities(
        self,
        task_id: str,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return recorded handles for one task, optionally paginated."""
        offset = max(0, int(offset))
        pagination = ""
        params: list[Any] = [task_id]
        if limit is not None:
            pagination = " LIMIT ? OFFSET ?"
            params.extend([max(1, min(int(limit), 500)), offset])
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM ai_task_entities
                WHERE task_id = ? ORDER BY id
                {pagination}
                """,
                params,
            ).fetchall()
        return [_decode_json_fields(dict(row)) for row in rows]

    def count_ai_task_entities(self, task_id: str) -> int:
        """Return the recorded entity count without loading entity JSON payloads."""
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM ai_task_entities WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return int(row["count"] if row is not None else 0)

    def update_ai_task_entity(
        self,
        task_id: str,
        handle: str,
        *,
        current_layer: str,
        formal_layer: str | None = None,
        metadata: Any | None = None,
    ) -> dict[str, Any]:
        """Update layer/provenance state after commit or reversible revert."""
        assignments = ["current_layer = ?"]
        params: list[Any] = [current_layer]
        if formal_layer is not None:
            assignments.append("formal_layer = ?")
            params.append(formal_layer)
        if metadata is not None:
            assignments.append("metadata = ?")
            params.append(_json(metadata))
        params.extend([task_id, handle])
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                f"""
                UPDATE ai_task_entities SET {", ".join(assignments)}
                WHERE task_id = ? AND handle = ?
                """,
                params,
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Task entity not found: {task_id}/{handle}")
            row = connection.execute(
                """
                SELECT * FROM ai_task_entities
                WHERE task_id = ? AND handle = ?
                """,
                (task_id, handle),
            ).fetchone()
        return _decode_json_fields(dict(row))

    def update_task_entities_and_status(
        self,
        task_id: str,
        *,
        entity_updates: list[dict[str, Any]],
        status: str,
    ) -> dict[str, Any]:
        """Atomically persist entity state changes and the task lifecycle state."""
        self._validate_task_status(status)
        with self._lock, self._connection() as connection:
            for update in entity_updates:
                assignments = ["current_layer = ?"]
                params: list[Any] = [update["current_layer"]]
                if "formal_layer" in update:
                    assignments.append("formal_layer = ?")
                    params.append(update["formal_layer"])
                if "metadata" in update:
                    assignments.append("metadata = ?")
                    params.append(_json(update["metadata"]))
                params.extend([task_id, update["handle"]])
                cursor = connection.execute(
                    f"""
                    UPDATE ai_task_entities SET {", ".join(assignments)}
                    WHERE task_id = ? AND handle = ?
                    """,
                    params,
                )
                if cursor.rowcount != 1:
                    raise KeyError(f"Task entity not found: {task_id}/{update['handle']}")
            cursor = connection.execute(
                """
                UPDATE ai_tasks SET status = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (status, _utc_now(), task_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"AI task not found: {task_id}")
        return self.get_ai_task(task_id)

    def find_task_for_handles(self, handles: list[str]) -> str | None:
        """Find one task only when every supplied handle belongs to it."""
        if not handles:
            return None
        placeholders = ",".join("?" for _ in handles)
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT task_id, COUNT(DISTINCT handle) AS matched
                FROM ai_task_entities
                WHERE handle IN ({placeholders})
                GROUP BY task_id
                HAVING matched = ?
                """,
                [*handles, len(set(handles))],
            ).fetchall()
        if len(rows) != 1:
            return None
        return str(rows[0]["task_id"])

    @staticmethod
    def _validate_task_status(status: str) -> None:
        if status not in TASK_STATUSES:
            raise ValueError(f"status must be one of {sorted(TASK_STATUSES)}")

    @staticmethod
    def _validate_table(table: str) -> str:
        if table not in ALLOWED_TABLES:
            raise ValueError(f"table must be one of {sorted(ALLOWED_TABLES)}")
        return table
