"""Regression checks for reviewed exception boundaries without a CAD session."""

import json
from collections import Counter
from types import SimpleNamespace

import pytest

from adapters.mixins.export_mixin import ExportMixin
from adapters.mixins.file_mixin import FileMixin
from adapters.mixins.utility_mixin import SelectionSetManager, UtilityMixin
from cad_memory.executor import undo_group
from mcp_tools import helpers


def test_encoding_configures_remaining_streams_after_unsupported_stdin(monkeypatch):
    encodings = []
    stream = SimpleNamespace(reconfigure=lambda **kwargs: encodings.append(kwargs["encoding"]))
    monkeypatch.setattr(
        helpers,
        "sys",
        SimpleNamespace(platform="win32", stdin=object(), stdout=stream, stderr=stream),
    )
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    helpers.setup_utf8_encoding()
    assert encodings == ["utf-8", "utf-8"]


@pytest.mark.parametrize(
    "activation_works,same_name", [(False, False), (True, False), (False, True)]
)
def test_switch_checks_active_document_when_setter_is_unsupported(
    monkeypatch, activation_works, same_name
):
    import pythoncom

    old = SimpleNamespace(Name="target.dwg" if same_name else "old.dwg", FullName="C:/old/old.dwg")
    target = SimpleNamespace(Name="target.dwg", FullName="C:/target/target.dwg")

    class Application:
        def __init__(self):
            self.active = old
            self.Documents = [target]

        @property
        def ActiveDocument(self):  # noqa: N802 - CAD COM spelling
            return self.active

        @ActiveDocument.setter
        def ActiveDocument(self, _value):  # noqa: N802
            raise RuntimeError("This CAD exposes a read-only ActiveDocument")

    app = Application()

    def activate():
        if activation_works:
            app.active = target

    target.Activate = activate

    class Adapter(FileMixin):
        document = old

        def _get_application(self, _operation):
            return app

    monkeypatch.setattr(pythoncom, "PumpWaitingMessages", lambda: None)
    adapter = Adapter()
    assert adapter.switch_drawing("target.dwg") is activation_works
    assert adapter.document is (target if activation_works else old)


def test_refused_selection_deletion_stops_recreation():
    calls = []

    def refused_delete():
        raise RuntimeError("Selection set is still in use")

    existing = SimpleNamespace(Delete=refused_delete)
    selection_sets = SimpleNamespace(
        Item=lambda _name: existing,
        Add=lambda name: calls.append(name),
    )
    document = SimpleNamespace(SelectionSets=selection_sets)
    with pytest.raises(RuntimeError, match="still in use"):
        with SelectionSetManager(document, "TEMP"):
            pytest.fail("A refused cleanup must not enter the selection body")
    with pytest.raises(RuntimeError, match="still in use"):
        UtilityMixin()._delete_selection_set(document, "TEMP")
    assert calls == []


def test_undo_cleanup_preserves_the_primary_failure():
    def refused_end():
        raise RuntimeError("Undo marks are unavailable")

    document = SimpleNamespace(StartUndoMark=lambda: None, EndUndoMark=refused_end)
    adapter = SimpleNamespace(_get_document=lambda _operation: document)
    with pytest.raises(ValueError, match="original operation failure"):
        with undo_group(adapter):
            raise ValueError("original operation failure")


class ExportAdapter(ExportMixin):
    @staticmethod
    def _fast_get_property(entity, name, default=None):
        return getattr(entity, name, default)


def export_row(monkeypatch, **properties):
    import win32com.client

    monkeypatch.setattr(win32com.client.dynamic, "Dispatch", lambda entity: entity)
    entity = SimpleNamespace(Handle="A1", Layer="0", Color=256, **properties)
    stats = {"total_calls": 0, "properties_skipped": 0, "calls_by_type": Counter()}
    return ExportAdapter()._extract_single_entity_data(
        entity,
        do_timing=False,
        color_map_reverse={},
        perf_property_times=Counter(),
        com_call_stats=stats,
    )


@pytest.mark.parametrize("radius", [None, "not numeric", float("inf"), float("nan"), -1])
def test_invalid_circle_measurements_export_as_unknown(monkeypatch, radius):
    row = export_row(monkeypatch, ObjectName="AcDbCircle", Radius=radius)
    assert row["Radius"] is None
    assert row["Area"] is None
    assert row["Circumference"] is None
    assert "Radius" in row["GeometryErrors"]
    json.dumps(row, allow_nan=False)


def test_line_can_recover_length_from_independent_endpoints(monkeypatch):
    row = export_row(
        monkeypatch,
        ObjectName="AcDbLine",
        Length="invalid",
        StartPoint=(0, 0, 0),
        EndPoint=(3, 4, 0),
    )
    assert row["Length"] == 5.0
    assert "GeometryErrors" not in row


@pytest.mark.parametrize("endpoint", [None, (float("inf"), 0, 0)])
def test_real_zero_length_is_preserved_when_optional_endpoints_are_invalid(monkeypatch, endpoint):
    row = export_row(
        monkeypatch, ObjectName="AcDbLine", Length=0, StartPoint=(0, 0, 0), EndPoint=endpoint
    )
    assert row["Length"] == 0.0
    assert "GeometryErrors" not in row


def test_missing_length_and_endpoints_remain_unknown(monkeypatch):
    row = export_row(monkeypatch, ObjectName="AcDbLine", Length=None)
    assert row["Length"] is None
    assert "Length" in row["GeometryErrors"]
