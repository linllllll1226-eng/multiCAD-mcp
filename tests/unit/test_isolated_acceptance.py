"""Lifecycle state regressions using fake documents, never a live AutoCAD session."""

# ruff: noqa: N802 -- fake objects deliberately expose the AutoCAD COM API names.

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cad_memory import acceptance


class Document:
    def __init__(self, app, path="", count=0):
        """Create a fake drawing with observable mutations and save failures."""
        self.app = app
        self.FullName = str(path)
        self.Name = Path(path).name if path else "Drawing1.dwg"
        self.ModelSpace = SimpleNamespace(Count=count)
        self.Saved = bool(path)
        self.dbmod = 0
        self.writes = []
        self.fail_save = False

    def GetVariable(self, name):
        assert name == "DBMOD"
        return self.dbmod

    def SetVariable(self, name, value):
        self.writes.append((name, value))

    def SaveAs(self, path):
        self.FullName = path
        self.Name = Path(path).name
        self.Save()

    def Save(self):
        self.writes.append("save")
        if self.fail_save:
            raise RuntimeError("save failed")
        Path(self.FullName).write_text(str(self.ModelSpace.Count), encoding="utf-8")
        self.Saved = True
        self.dbmod = 0

    def Close(self, save):
        assert save is False
        self.writes.append("close")
        self.app.Documents.items.remove(self)
        self.app.ActiveDocument = self.app.Documents.items[-1]


class Documents:
    def __init__(self, app):
        """Keep a fake open-document collection with a generic Open return value."""
        self.app = app
        self.items = []
        self.open_calls = []
        self.add_calls = 0
        self.template_count = 0
        self.fail_after_open = False

    @property
    def Count(self):
        return len(self.items)

    def Item(self, index):
        return self.items[index]

    def Add(self):
        self.add_calls += 1
        doc = Document(self.app, count=self.template_count)
        self.items.append(doc)
        self.app.ActiveDocument = doc
        return doc

    def Open(self, path):
        self.open_calls.append(path)
        doc = Document(self.app, path, int(Path(path).read_text(encoding="utf-8")))
        self.items.append(doc)
        self.app.ActiveDocument = doc
        if self.fail_after_open:
            raise RuntimeError("Open returned an error after opening")
        return object()  # AutoCAD can return a generic wrapper; reacquire the document.


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(acceptance.os, "getpid", lambda: 101)
    app = SimpleNamespace()
    app.Documents = Documents(app)
    formal = Document(app, tmp_path / "formal.dwg", 7)
    formal.Save()
    formal.writes.clear()
    app.Documents.items.append(formal)
    app.ActiveDocument = formal
    root = tmp_path / "evidence"
    return app, root, formal


def state(root):
    return json.loads((root / "test_document_state.json").read_text(encoding="utf-8"))


def prepare_close(run):
    app, root, _ = run
    acceptance.execute_lifecycle("prepare", app, root)
    app.ActiveDocument.ModelSpace.Count = 39
    app.ActiveDocument.Saved = False
    app.ActiveDocument.dbmod = 1
    return acceptance.execute_lifecycle("save_close", app, root)


def test_full_lifecycle_binds_saved_bytes_and_requires_another_pid(run, monkeypatch):
    app, root, formal = run
    closed = prepare_close(run)
    assert closed["saved_drawing"]["entity_count"] == 39
    assert closed["baseline_preserved"] is True
    with pytest.raises(ValueError, match="different server process"):
        acceptance.execute_lifecycle("reopen", app, root)
    assert not app.Documents.open_calls
    monkeypatch.setattr(acceptance.os, "getpid", lambda: 202)
    reopened = acceptance.execute_lifecycle("reopen", app, root)
    assert reopened["independent_process"] is True
    assert reopened["geometry_verified"] is False
    assert reopened["drawing"]["entity_count"] == 39
    assert reopened["drawing"]["file_sha256"] == closed["saved_sha256"]
    acceptance.execute_lifecycle("snapshot", app, root)
    assert state(root)["phase"] == "reopened"
    assert formal.writes == []
    assert Path(formal.FullName).read_text(encoding="utf-8") == "7"


def test_wrong_active_document_never_saves_or_closes_formal_drawing(run):
    app, root, formal = run
    acceptance.execute_lifecycle("prepare", app, root)
    app.ActiveDocument = formal
    with pytest.raises(ValueError, match="Active document"):
        acceptance.execute_lifecycle("save_close", app, root)
    assert formal.writes == []
    assert state(root)["phase"] == "prepared"


@pytest.mark.parametrize("action", ["prepare", "save_close", "snapshot"])
def test_closed_state_cannot_be_overwritten_or_reset(run, action):
    app, root, _ = run
    prepare_close(run)
    before = state(root)
    with pytest.raises(ValueError):
        acceptance.execute_lifecycle(action, app, root)
    assert state(root) == before
    assert app.Documents.add_calls == 1


def test_file_tampering_stops_before_open(run, monkeypatch):
    app, root, _ = run
    prepare_close(run)
    Path(state(root)["path"]).write_text("changed", encoding="utf-8")
    monkeypatch.setattr(acceptance.os, "getpid", lambda: 202)
    with pytest.raises(ValueError, match="changed before reopen"):
        acceptance.execute_lifecycle("reopen", app, root)
    assert not app.Documents.open_calls
    assert state(root)["phase"] == "closed"


def test_save_failure_persists_pending_state_and_blocks_retry(run):
    app, root, _ = run
    acceptance.execute_lifecycle("prepare", app, root)
    target = app.ActiveDocument
    target.fail_save = True
    with pytest.raises(RuntimeError, match="save failed"):
        acceptance.execute_lifecycle("save_close", app, root)
    assert state(root)["phase"] == "saving"
    assert "close" not in target.writes
    before = list(target.writes)
    with pytest.raises(ValueError, match="requires phase"):
        acceptance.execute_lifecycle("save_close", app, root)
    assert target.writes == before
    assert not (root / ".acceptance.lock").exists()


def test_nonempty_template_stops_before_setting_variables_or_saving(run):
    app, root, _ = run
    app.Documents.template_count = 1
    with pytest.raises(ValueError, match="template contains entities"):
        acceptance.execute_lifecycle("prepare", app, root)
    assert app.ActiveDocument.writes == []
    assert state(root)["phase"] == "preparing"
    assert not Path(state(root)["path"]).exists()


def test_baseline_change_stops_before_save(run):
    app, root, formal = run
    acceptance.execute_lifecycle("prepare", app, root)
    formal.dbmod = 1
    before = list(app.ActiveDocument.writes)
    with pytest.raises(ValueError, match="Existing document state changed"):
        acceptance.execute_lifecycle("save_close", app, root)
    assert app.ActiveDocument.writes == before
    assert state(root)["phase"] == "prepared"


def test_failed_open_recovery_requires_clean_file_and_new_process(run, monkeypatch):
    app, root, formal = run
    prepare_close(run)
    monkeypatch.setattr(acceptance.os, "getpid", lambda: 202)
    app.Documents.fail_after_open = True
    with pytest.raises(RuntimeError, match="after opening"):
        acceptance.execute_lifecycle("reopen", app, root)
    assert state(root)["phase"] == "reopening"
    assert not any(event.get("reopened") for event in state(root)["events"])
    target = app.ActiveDocument
    target.dbmod = 1
    with pytest.raises(ValueError, match="clean saved state"):
        acceptance.execute_lifecycle("reclose", app, root)
    assert "close" not in target.writes
    target.dbmod = 0
    acceptance.execute_lifecycle("reclose", app, root)
    assert state(root)["closed_by_pid"] == 202
    with pytest.raises(ValueError, match="different server process"):
        acceptance.execute_lifecycle("reopen", app, root)
    monkeypatch.setattr(acceptance.os, "getpid", lambda: 303)
    app.Documents.fail_after_open = False
    result = acceptance.execute_lifecycle("reopen", app, root)
    assert result["reopened"]
    assert formal.writes == []


def test_existing_lock_refuses_operations_and_is_preserved(run):
    app, root, _ = run
    root.mkdir()
    lock = root / ".acceptance.lock"
    lock.write_text("other process", encoding="utf-8")
    with pytest.raises(ValueError, match="locked"):
        acceptance.execute_lifecycle("prepare", app, root)
    assert lock.read_text(encoding="utf-8") == "other process"
    assert app.Documents.add_calls == 0


@pytest.mark.parametrize("change", ["schema", "run_id", "path"])
def test_target_guard_rejects_rebinding(run, change):
    app, root, formal = run
    acceptance.execute_lifecycle("prepare", app, root)
    record = state(root)
    if change == "schema":
        record["schema_version"] = 0
    elif change == "run_id":
        record["run_id"] = "../formal"
    else:
        record["path"] = formal.FullName
    with pytest.raises(ValueError):
        acceptance.guard_target(record, root)


@pytest.mark.parametrize("change", ["missing_event", "pid", "hash", "phase"])
def test_independent_guard_requires_bound_close_evidence(run, monkeypatch, change):
    _, root, _ = run
    prepare_close(run)
    record = state(root)
    monkeypatch.setattr(acceptance.os, "getpid", lambda: 202)
    if change == "missing_event":
        record["events"] = []
    elif change == "pid":
        record["events"][-1]["server_pid"] = 999
    elif change == "hash":
        record["saved_sha256"] = "different"
    else:
        record["phase"] = "prepared"
    with pytest.raises(ValueError):
        acceptance.guard_independent_reopen(record)


def test_already_open_is_not_a_reopen(run, monkeypatch):
    app, root, _ = run
    prepare_close(run)
    app.Documents.Open(state(root)["path"])
    monkeypatch.setattr(acceptance.os, "getpid", lambda: 202)
    with pytest.raises(ValueError, match="already open"):
        acceptance.execute_lifecycle("reopen", app, root)
    assert len(app.Documents.open_calls) == 1
    assert state(root)["phase"] == "closed"


def test_close_failure_never_records_success(run, monkeypatch):
    app, root, _ = run
    acceptance.execute_lifecycle("prepare", app, root)

    def failing_close(_save):
        raise RuntimeError("close failed")

    monkeypatch.setattr(app.ActiveDocument, "Close", failing_close)
    with pytest.raises(RuntimeError, match="close failed"):
        acceptance.execute_lifecycle("save_close", app, root)
    record = state(root)
    assert record["phase"] == "closing"
    assert not any(event.get("closed") for event in record["events"])
    with pytest.raises(ValueError, match="recorded independent close"):
        acceptance.guard_independent_reopen(record)
