"""Opt-in test-DWG lifecycle; geometry verification remains a separate CAD operation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def sha256(path: str | Path) -> str:
    """Hash the saved file rather than a cached entity snapshot."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def file_identity(document: Any) -> dict[str, Any]:
    """Read observed identity, save state, count and saved bytes without editing CAD."""
    full = str(document.FullName)
    return {
        "name": str(document.Name),
        "path": full,
        "saved": bool(document.Saved),
        "dbmod": int(document.GetVariable("DBMOD")),
        "entity_count": int(document.ModelSpace.Count),
        "file_sha256": sha256(full) if Path(full).is_file() else None,
    }


def guard_target(state: dict[str, Any], root: Path, document: Any = None) -> Path:
    """Bind all lifecycle operations to this run's generated test filename."""
    run_id = state.get("run_id", "")
    if state.get("schema_version") != 1 or not re.fullmatch(r"[a-f0-9]{32}", str(run_id)):
        raise ValueError("Invalid acceptance run identity; use a fresh evidence directory")
    root = root.resolve()
    target = Path(state["path"]).resolve()
    if target != root / f"TEST_fixture_block_{run_id}.dwg":
        raise ValueError("Lifecycle target does not match this run's isolated test DWG")
    if document is not None and Path(str(document.FullName)).resolve() != target:
        raise ValueError("Active document does not match the generated test DWG")
    return target


def guard_independent_reopen(state: dict[str, Any]) -> None:
    """Require a successful close recorded by another operating-system process."""
    if state.get("phase") != "closed":
        raise ValueError("Reopen requires a recorded independent close")
    closing_pid = state.get("closed_by_pid")
    if type(closing_pid) is not int or closing_pid <= 0 or closing_pid == os.getpid():
        raise ValueError("Reopen must run in a different server process from the close")
    closes = [event for event in state.get("events", []) if event.get("closed") is True]
    if not closes or closes[-1].get("server_pid") != closing_pid:
        raise ValueError("Close process identity is not backed by a successful close event")
    if closes[-1].get("saved_sha256") != state.get("saved_sha256"):
        raise ValueError("Close event does not match the saved file hash")


def _write_state(path: Path, state: dict[str, Any]) -> None:
    temporary = path.with_name(f".acceptance-{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _documents(app: Any) -> list[dict[str, Any]]:
    return [file_identity(app.Documents.Item(i)) for i in range(app.Documents.Count)]


def _assert_baseline(app: Any, state: dict[str, Any], target: Path) -> list[dict[str, Any]]:
    others = [item for item in _documents(app) if Path(item["path"]).resolve() != target]
    if sorted(others, key=lambda item: item["path"]) != sorted(
        state["baseline"], key=lambda item: item["path"]
    ):
        raise ValueError("Existing document state changed during acceptance")
    return others


def _clean_target(app: Any, state: dict[str, Any], root: Path) -> tuple[Any, dict[str, Any]]:
    document = app.ActiveDocument
    guard_target(state, root, document)
    identity = file_identity(document)
    if not identity["saved"] or identity["dbmod"] != 0:
        raise ValueError("Test document is not in a clean saved state")
    return document, identity


def execute_lifecycle(action: str, app: Any, root: Path) -> dict[str, Any]:
    """Run one explicitly requested action on a fresh test DWG with a local state lock.

    A successful reopen proves process and file lifecycle only. It does not prove
    entity geometry, source completeness, or production release readiness.
    """
    if action not in {"prepare", "snapshot", "save_close", "reopen", "reclose"}:
        raise ValueError("Supported actions: prepare, snapshot, save_close, reopen, reclose")
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".acceptance.lock"
    try:
        with lock.open("x", encoding="utf-8") as stream:
            stream.write(str(os.getpid()))
    except FileExistsError as exc:
        raise ValueError("Acceptance run is locked; inspect it before retrying") from exc
    try:
        return _execute(action, app, root)
    finally:
        lock.unlink()


def _execute(action: str, app: Any, root: Path) -> dict[str, Any]:
    path = root / "test_document_state.json"
    documents = app.Documents
    stamp = datetime.now(timezone.utc).isoformat()
    if action == "prepare":
        if path.exists():
            raise ValueError("Run already exists; refusing to replace its test drawing")
        run_id = uuid4().hex
        target = root / f"TEST_fixture_block_{run_id}.dwg"
        if target.exists():
            raise ValueError("Refusing to overwrite an existing file")
        state: dict[str, Any] = {
            "schema_version": 1,
            "run_id": run_id,
            "path": str(target),
            "baseline": _documents(app),
            "events": [],
            "phase": "preparing",
        }
        _write_state(path, state)
        document = documents.Add()
        if int(document.ModelSpace.Count) != 0:
            raise ValueError("The new drawing template contains entities; stopped")
        for name, value in {
            "INSUNITS": 4,
            "MEASUREMENT": 1,
            "DIMTXT": 2.0,
            "DIMASZ": 1.5,
            "DIMDEC": 1,
            "DIMZIN": 8,
            "DIMSCALE": 1.0,
        }.items():
            document.SetVariable(name, value)
        if target.exists():
            raise ValueError("Refusing to overwrite an existing file")
        document.SaveAs(str(target))
        document, identity = _clean_target(app, state, root)
        _assert_baseline(app, state, target)
        state["phase"] = "prepared"
        result = {"prepared": True, "drawing": identity, "baseline": state["baseline"]}
    else:
        state = json.loads(path.read_text(encoding="utf-8"))
        target = guard_target(state, root)
        if action == "snapshot":
            if state.get("phase") not in {"prepared", "reopened"}:
                raise ValueError("Snapshot requires a prepared or reopened test drawing")
            guard_target(state, root, app.ActiveDocument)
            result = {"drawing": file_identity(app.ActiveDocument)}
        elif action in {"save_close", "reclose"}:
            expected_phase = "prepared" if action == "save_close" else "reopening"
            if state.get("phase") != expected_phase:
                raise ValueError(f"{action} requires phase {expected_phase}")
            document = app.ActiveDocument
            guard_target(state, root, document)
            _assert_baseline(app, state, target)
            if action == "save_close":
                state["phase"] = "saving"
                _write_state(path, state)
                document.Save()
                document, identity = _clean_target(app, state, root)
                state["saved_sha256"] = identity["file_sha256"]
            else:
                document, identity = _clean_target(app, state, root)
                if identity["file_sha256"] != state.get("saved_sha256"):
                    raise ValueError("Recovery target has changed; refusing to close")
            if not state.get("saved_sha256"):
                raise ValueError("No saved file hash; refusing to close")
            state["phase"] = "closing"
            _write_state(path, state)
            document.Close(False)
            remaining = _documents(app)
            if any(Path(item["path"]).resolve() == target for item in remaining):
                raise ValueError("Target remained open after close")
            _assert_baseline(app, state, target)
            if sha256(target) != state["saved_sha256"]:
                raise ValueError("Saved file changed during close")
            state["phase"] = "closed"
            state["closed_by_pid"] = os.getpid()
            result = {
                "closed": True,
                "saved_drawing": identity,
                "saved_sha256": state["saved_sha256"],
                "remaining_drawings": remaining,
                "baseline_preserved": True,
            }
        else:
            guard_independent_reopen(state)
            if sha256(target) != state.get("saved_sha256"):
                raise ValueError("Saved file changed before reopen")
            names_before = [str(documents.Item(i).FullName) for i in range(documents.Count)]
            if any(Path(name).resolve() == target for name in names_before):
                raise ValueError("Target is already open; cannot prove reopen")
            _assert_baseline(app, state, target)
            state["phase"] = "reopening"
            _write_state(path, state)
            documents.Open(str(target))
            # Ignore the generic Open return wrapper and reacquire ActiveDocument.
            deadline = time.monotonic() + 10.0
            while True:
                try:
                    document = app.ActiveDocument
                    guard_target(state, root, document)
                    break
                except (AttributeError, ValueError):
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.1)
            document, identity = _clean_target(app, state, root)
            if identity["file_sha256"] != state["saved_sha256"]:
                raise ValueError("Saved file changed during reopen")
            _assert_baseline(app, state, target)
            state["phase"] = "reopened"
            result = {
                "reopened": True,
                "independent_process": True,
                "geometry_verified": False,
                "drawing": identity,
                "open_before": names_before,
                "closed_by_pid": state["closed_by_pid"],
            }
    event = {"action": action, "utc": stamp, "server_pid": os.getpid(), **result}
    state["events"].append(event)
    _write_state(path, state)
    return event
