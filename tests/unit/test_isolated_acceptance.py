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
        self.Path = str(Path(path).parent) if path else ""
        self._oleobj_ = object()
        self.ModelSpace = SimpleNamespace(Count=count)
        self.Saved = bool(path)
        self.dbmod = 0
        self.writes = []
        self.save_as_calls = []
        self.fail_save = False

    def GetVariable(self, name):
        assert name == "DBMOD"
        return self.dbmod

    def SetVariable(self, name, value):
        self.writes.append((name, value))

    def SaveAs(self, path):
        self.save_as_calls.append(path)
        self.FullName = path
        self.Name = Path(path).name
        self.Path = str(Path(path).parent)
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


class ComReadError(RuntimeError):
    def __init__(self, hresult):
        """Expose a COM-style HRESULT without importing the native COM runtime."""
        super().__init__(f"COM read failed: {hresult}")
        self.hresult = hresult


class GenericAddWrapper:
    def __init__(self, document):
        """Expose only COM identity, as an opaque native Add wrapper can do."""
        self._oleobj_ = getattr(document, "_oleobj_", None)
        self._attribute_reads = []

    def __getattr__(self, name):
        """Reject every document property and method on the generic wrapper."""
        self._attribute_reads.append(name)
        raise AttributeError(f"Add.{name}")


class CountReadSequence:
    def __init__(self, outcomes):
        """Return or raise the provided outcomes, then repeat the final outcome."""
        self.outcomes = iter(outcomes)
        self.last = outcomes[-1]
        self.reads = 0
        self.errors_raised = 0

    @property
    def Count(self):
        self.reads += 1
        outcome = next(self.outcomes, self.last)
        if isinstance(outcome, Exception):
            self.errors_raised += 1
            raise outcome
        return outcome


def count_read_after_add(app, monkeypatch, outcomes):
    """Inject read failures only on the document returned by the single Add call."""
    reader = CountReadSequence(outcomes)
    original_add = app.Documents.Add

    def add():
        document = original_add()
        monkeypatch.setattr(document, "ModelSpace", reader)
        return document

    monkeypatch.setattr(app.Documents, "Add", add)
    return reader


def controlled_retry_clock(monkeypatch):
    """Advance the retry clock only when the lifecycle explicitly sleeps."""
    clock = SimpleNamespace(elapsed=0.0, sleeps=[])

    def sleep(seconds):
        clock.sleeps.append(seconds)
        clock.elapsed += seconds

    monkeypatch.setattr(
        acceptance, "time", SimpleNamespace(monotonic=lambda: clock.elapsed, sleep=sleep)
    )
    return clock


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
    assert app.ActiveDocument.save_as_calls == []
    assert app.Documents.add_calls == 1
    assert state(root)["phase"] == "preparing"
    assert not Path(state(root)["path"]).exists()
    assert not (root / ".acceptance.lock").exists()


def test_busy_new_document_read_retries_without_repeating_add(run, monkeypatch):
    app, root, formal = run
    clock = controlled_retry_clock(monkeypatch)
    reader = count_read_after_add(
        app, monkeypatch, [ComReadError(-2147418111), ComReadError(-2147417846), 0]
    )

    result = acceptance.execute_lifecycle("prepare", app, root)

    assert result["prepared"] is True
    assert result["drawing"]["entity_count"] == 0
    assert state(root)["phase"] == "prepared"
    assert app.Documents.add_calls == 1
    assert reader.errors_raised == 2
    assert clock.sleeps == [0.1, 0.1]
    assert app.ActiveDocument.save_as_calls == [state(root)["path"]]
    assert app.ActiveDocument.writes.count("save") == 1
    assert len([entry for entry in app.ActiveDocument.writes if isinstance(entry, tuple)]) == 7
    assert app.Documents.open_calls == []
    assert formal.writes == []
    assert not (root / ".acceptance.lock").exists()


@pytest.mark.parametrize(
    "error",
    [ComReadError(-2147467259), ComReadError("-2147418111"), RuntimeError(-2147418111)],
)
def test_nonbusy_or_noninteger_read_errors_fail_immediately(run, monkeypatch, error):
    app, root, formal = run
    clock = controlled_retry_clock(monkeypatch)
    reader = count_read_after_add(app, monkeypatch, [error, 0])

    with pytest.raises(RuntimeError) as caught:
        acceptance.execute_lifecycle("prepare", app, root)

    assert caught.value is error
    assert reader.reads == 1
    assert clock.sleeps == []
    assert app.Documents.add_calls == 1
    assert app.ActiveDocument.writes == []
    assert app.ActiveDocument.save_as_calls == []
    assert state(root)["phase"] == "preparing"
    assert state(root)["events"] == []
    assert not Path(state(root)["path"]).exists()
    assert formal.writes == []
    assert not (root / ".acceptance.lock").exists()


@pytest.mark.parametrize("hresult", [-2147418111, -2147417846])
def test_persistent_busy_read_stops_at_deadline_without_mutations_or_success(
    run, monkeypatch, hresult
):
    app, root, formal = run
    clock = controlled_retry_clock(monkeypatch)
    error = ComReadError(hresult)
    reader = count_read_after_add(app, monkeypatch, [error])

    with pytest.raises(TimeoutError, match="COM-unavailable for 10 seconds") as caught:
        acceptance.execute_lifecycle("prepare", app, root)

    assert caught.value.__cause__ is error
    assert clock.elapsed == pytest.approx(10.0)
    assert all(0 < duration <= 0.1 for duration in clock.sleeps)
    assert 100 <= reader.reads <= 102
    assert app.Documents.add_calls == 1
    assert app.Documents.open_calls == []
    assert app.ActiveDocument.writes == []
    assert app.ActiveDocument.save_as_calls == []
    assert app.ActiveDocument.FullName == ""
    assert state(root)["phase"] == "preparing"
    assert state(root)["events"] == []
    assert not Path(state(root)["path"]).exists()
    assert formal.writes == []
    assert not (root / ".acceptance.lock").exists()
    with pytest.raises(ValueError, match="Run already exists"):
        acceptance.execute_lifecycle("prepare", app, root)
    assert app.Documents.add_calls == 1


def test_busy_read_followed_by_nonempty_template_still_blocks_all_writes(run, monkeypatch):
    app, root, formal = run
    clock = controlled_retry_clock(monkeypatch)
    count_read_after_add(app, monkeypatch, [ComReadError(-2147418111), 1])

    with pytest.raises(ValueError, match="template contains entities"):
        acceptance.execute_lifecycle("prepare", app, root)

    assert app.Documents.add_calls == 1
    assert clock.sleeps == [0.1]
    assert app.ActiveDocument.writes == []
    assert app.ActiveDocument.save_as_calls == []
    assert state(root)["phase"] == "preparing"
    assert state(root)["events"] == []
    assert formal.writes == []
    assert not (root / ".acceptance.lock").exists()


def test_delayed_wakeup_does_not_retry_a_read_after_the_deadline(run, monkeypatch):
    app, root, formal = run
    clock = SimpleNamespace(elapsed=0.0)

    def delayed_sleep(_seconds):
        clock.elapsed = 10.1

    monkeypatch.setattr(
        acceptance,
        "time",
        SimpleNamespace(monotonic=lambda: clock.elapsed, sleep=delayed_sleep),
    )
    reader = count_read_after_add(app, monkeypatch, [ComReadError(-2147418111), 0])

    with pytest.raises(TimeoutError, match="COM-unavailable for 10 seconds"):
        acceptance.execute_lifecycle("prepare", app, root)

    assert reader.reads == 1
    assert app.Documents.add_calls == 1
    assert app.ActiveDocument.writes == []
    assert app.ActiveDocument.save_as_calls == []
    assert state(root)["phase"] == "preparing"
    assert state(root)["events"] == []
    assert formal.writes == []
    assert not (root / ".acceptance.lock").exists()


@pytest.mark.parametrize("error", [ComReadError(-2147418111), AttributeError("<unknown>.Item")])
def test_baseline_read_retries_the_whole_enumeration_with_fresh_items(run, monkeypatch, error):
    app, root, formal = run
    clock = controlled_retry_clock(monkeypatch)
    other = Document(app, root.parent / "formal2.dwg", 9)
    other.Save()
    other.writes.clear()
    app.Documents.items.append(other)
    original_item = app.Documents.Item
    indices = []

    def item(index):
        indices.append(index)
        if indices == [0, 1]:
            formal.ModelSpace.Count = 8
            raise error
        return original_item(index)

    monkeypatch.setattr(app.Documents, "Item", item)
    result = acceptance.execute_lifecycle("prepare", app, root)

    assert result["prepared"] is True
    assert indices == [0, 1, 0, 1, 0, 1, 2]
    assert [entry["entity_count"] for entry in result["baseline"]] == [8, 9]
    assert clock.sleeps == [0.1]
    assert app.Documents.add_calls == 1
    assert formal.writes == other.writes == []


@pytest.mark.parametrize("error", [ComReadError(-2147418111), AttributeError("Item.ModelSpace")])
def test_permanent_baseline_read_timeout_stops_before_add_or_any_mutation(run, monkeypatch, error):
    app, root, formal = run
    clock = controlled_retry_clock(monkeypatch)
    calls = []

    def item(index):
        calls.append(index)
        raise error

    monkeypatch.setattr(app.Documents, "Item", item)
    with pytest.raises(TimeoutError, match="COM-unavailable for 10 seconds") as caught:
        acceptance.execute_lifecycle("prepare", app, root)

    assert caught.value.__cause__ is error
    assert clock.elapsed == pytest.approx(10.0)
    assert 100 <= len(calls) <= 102
    assert all(0 < duration <= 0.1 for duration in clock.sleeps)
    assert app.Documents.add_calls == 0
    assert formal.writes == formal.save_as_calls == []
    assert not (root / "test_document_state.json").exists()
    assert not (root / ".acceptance.lock").exists()


def test_baseline_value_error_is_not_retried(run, monkeypatch):
    app, root, formal = run
    clock = controlled_retry_clock(monkeypatch)
    error = ValueError("invalid baseline observation")
    calls = []

    def item(index):
        calls.append(index)
        raise error

    monkeypatch.setattr(app.Documents, "Item", item)
    with pytest.raises(ValueError) as caught:
        acceptance.execute_lifecycle("prepare", app, root)

    assert caught.value is error
    assert calls == [0]
    assert clock.sleeps == []
    assert app.Documents.add_calls == 0
    assert formal.writes == []
    assert not (root / "test_document_state.json").exists()
    assert not (root / ".acceptance.lock").exists()


@pytest.mark.parametrize("member", ["Name", "ModelSpace", "SaveAs"])
def test_native_add_observation_recovers_unavailable_member_before_any_write(
    run, monkeypatch, member
):
    app, root, formal = run
    clock = controlled_retry_clock(monkeypatch)
    original_add = app.Documents.Add
    observations = []

    class NativeProxy:
        def __init__(self, document):
            self.document = document

        def __getattr__(self, name):
            if name == member:
                observations.append(list(self.document.writes))
                if len(observations) == 1:
                    raise AttributeError(f"<unknown>.{name}")
            return getattr(self.document, name)

    def add():
        document = original_add()
        app.ActiveDocument = NativeProxy(document)
        return GenericAddWrapper(document)

    monkeypatch.setattr(app.Documents, "Add", add)
    result = acceptance.execute_lifecycle("prepare", app, root)

    assert result["prepared"] is True
    assert observations[:2] == [[], []]
    assert clock.sleeps == [0.1]
    assert app.Documents.add_calls == 1
    assert app.ActiveDocument.save_as_calls == [state(root)["path"]]
    assert app.ActiveDocument.writes.count("save") == 1
    assert formal.writes == []


def test_native_retry_rebinds_com_identity_and_rejects_same_name_wrong_document(run, monkeypatch):
    app, root, formal = run
    clock = controlled_retry_clock(monkeypatch)
    original_sleep = acceptance.time.sleep
    reader = count_read_after_add(app, monkeypatch, [AttributeError("<unknown>.ModelSpace"), 0])
    other = Document(app)

    def sleep(seconds):
        original_sleep(seconds)
        app.ActiveDocument = other

    monkeypatch.setattr(acceptance.time, "sleep", sleep)
    with pytest.raises(ValueError, match="COM identity"):
        acceptance.execute_lifecycle("prepare", app, root)

    assert clock.sleeps == [0.1]
    assert reader.reads == 1
    assert app.Documents.add_calls == 1
    assert app.Documents.items[-1].writes == other.writes == formal.writes == []
    assert state(root)["phase"] == "preparing"
    assert state(root)["events"] == []


@pytest.mark.parametrize("operation", ["clean_target", "snapshot"])
def test_bound_saved_observation_recovers_a_transient_read_without_writes(
    run, monkeypatch, operation
):
    app, root, formal = run
    acceptance.execute_lifecycle("prepare", app, root)
    document = app.ActiveDocument
    before = list(document.writes)
    clock = controlled_retry_clock(monkeypatch)
    document.ModelSpace = CountReadSequence([AttributeError("Item.ModelSpace"), 0])

    if operation == "snapshot":
        identity = acceptance.execute_lifecycle("snapshot", app, root)["drawing"]
    else:
        observed, identity = acceptance._clean_target(app, state(root), root)
        assert observed is document

    assert identity["path"] == state(root)["path"]
    assert identity["entity_count"] == 0
    assert clock.sleeps == [0.1]
    assert document.writes == before
    assert app.Documents.add_calls == 1
    assert formal.writes == []


@pytest.mark.parametrize("operation", ["clean_target", "snapshot"])
def test_bound_observation_retry_rechecks_target_and_stops_on_guard_failure(
    run, monkeypatch, operation
):
    app, root, formal = run
    acceptance.execute_lifecycle("prepare", app, root)
    document = app.ActiveDocument
    before = list(document.writes)
    before_state = state(root)
    clock = controlled_retry_clock(monkeypatch)
    original_sleep = acceptance.time.sleep
    reader = CountReadSequence([AttributeError("Item.ModelSpace"), 0])
    document.ModelSpace = reader

    def sleep(seconds):
        original_sleep(seconds)
        app.ActiveDocument = formal

    monkeypatch.setattr(acceptance.time, "sleep", sleep)
    with pytest.raises(ValueError, match="Active document"):
        if operation == "snapshot":
            acceptance.execute_lifecycle("snapshot", app, root)
        else:
            acceptance._clean_target(app, state(root), root)

    assert clock.sleeps == [0.1]
    assert reader.reads == 1
    assert state(root) == before_state
    assert document.writes == before
    assert formal.writes == []


@pytest.mark.parametrize("error", [ComReadError(-2147418111), AttributeError("SaveAs unavailable")])
def test_retryable_read_error_from_saveas_operation_is_never_retried(run, monkeypatch, error):
    app, root, formal = run
    clock = controlled_retry_clock(monkeypatch)
    original_add = app.Documents.Add
    calls = []

    def save_as(path):
        calls.append(path)
        raise error

    def add():
        document = original_add()
        document.SaveAs = save_as
        return document

    monkeypatch.setattr(app.Documents, "Add", add)
    with pytest.raises(type(error)) as caught:
        acceptance.execute_lifecycle("prepare", app, root)

    assert caught.value is error
    assert calls == [state(root)["path"]]
    assert clock.sleeps == []
    assert app.Documents.add_calls == 1
    assert len(app.ActiveDocument.writes) == 7
    assert state(root)["phase"] == "preparing"
    assert state(root)["events"] == []
    assert formal.writes == []
    assert not (root / ".acceptance.lock").exists()


@pytest.mark.parametrize("default_path", ["", r"C:\Windows\system32"])
def test_generic_add_wrapper_is_rebound_to_the_same_native_document_before_writes(
    run, monkeypatch, default_path
):
    app, root, formal = run
    original_add = app.Documents.Add
    wrappers = []

    def add():
        document = original_add()
        document.Path = default_path
        wrapper = GenericAddWrapper(document)
        wrappers.append(wrapper)
        return wrapper

    monkeypatch.setattr(app.Documents, "Add", add)
    result = acceptance.execute_lifecycle("prepare", app, root)

    assert result["prepared"] is True
    assert result["drawing"]["entity_count"] == 0
    assert app.Documents.add_calls == 1
    assert wrappers[0]._attribute_reads == []
    assert app.ActiveDocument.save_as_calls == [state(root)["path"]]
    assert app.ActiveDocument.writes.count("save") == 1
    assert len([entry for entry in app.ActiveDocument.writes if isinstance(entry, tuple)]) == 7
    assert state(root)["phase"] == "prepared"
    assert formal.writes == []
    assert not (root / ".acceptance.lock").exists()


@pytest.mark.parametrize(
    "invalid",
    [
        "empty_native_name",
        "baseline_name",
        "saved_fullname",
        "different_wrapper_com_identity",
        "wrong_active_baseline",
        "wrong_active_other_empty",
        "wrong_active_same_name",
        "native_saveas_missing",
        "native_setvariable_missing",
        "missing_com_identity",
        "active_nonempty",
    ],
)
def test_unbound_add_or_active_document_never_receives_variables_or_save(run, monkeypatch, invalid):
    app, root, formal = run
    original_add = app.Documents.Add
    created = []

    def add():
        document = original_add()
        created.append(document)
        if invalid == "empty_native_name":
            document.Name = ""
        elif invalid == "baseline_name":
            document.Name = formal.Name
        elif invalid == "saved_fullname":
            document.FullName = str(root / "unrelated.dwg")
        elif invalid == "different_wrapper_com_identity":
            wrapper = GenericAddWrapper(document)
            wrapper._oleobj_ = object()
            return wrapper
        elif invalid == "wrong_active_baseline":
            app.ActiveDocument = formal
        elif invalid in {"wrong_active_other_empty", "wrong_active_same_name"}:
            other = Document(app)
            if invalid == "wrong_active_other_empty":
                other.Name = "Drawing99.dwg"
            app.Documents.items.append(other)
            app.ActiveDocument = other
        elif invalid == "native_saveas_missing":
            monkeypatch.setattr(document, "SaveAs", None)
        elif invalid == "native_setvariable_missing":
            monkeypatch.setattr(document, "SetVariable", None)
        elif invalid == "missing_com_identity":
            monkeypatch.delattr(document, "_oleobj_")
        else:
            document.ModelSpace.Count = 1
        return GenericAddWrapper(document)

    monkeypatch.setattr(app.Documents, "Add", add)
    with pytest.raises(ValueError):
        acceptance.execute_lifecycle("prepare", app, root)

    assert app.Documents.add_calls == 1
    assert created[0].writes == []
    assert created[0].save_as_calls == []
    assert app.ActiveDocument.writes == []
    assert app.ActiveDocument.save_as_calls == []
    assert state(root)["phase"] == "preparing"
    assert state(root)["events"] == []
    assert not Path(state(root)["path"]).exists()
    assert formal.writes == []
    assert not (root / ".acceptance.lock").exists()


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
