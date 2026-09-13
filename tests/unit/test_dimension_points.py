"""Dimension definitions require fresh, identity-bound real DXF evidence."""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from cad_memory import dimension_points
from cad_memory.dimension_points import _lisp_string, parse_definition_points
from cad_memory.models import DrawingPlan
from cad_memory.verifier import PostExecutionVerifier


def response():
    return "nonce\n451\nD:/test.dwg\n3\n5\n6\n0\n-5\n-6\n0\nDONE\n"


def parse(text, **kwargs):
    args = dict(
        nonce="nonce", handle="451", drawing="D:/test.dwg", object_type="AcDbDiametricDimension"
    )
    return parse_definition_points(text, **(args | kwargs))


def test_diameter_points_have_correct_orientation():
    points = parse(response())
    assert points["chord_point"] == [-5, -6, 0]
    assert points["far_chord_point"] == [5, 6, 0]


def test_radial_group_10_is_center():
    points = parse(response().replace("\n3\n", "\n4\n"), object_type="AcDbRadialDimension")
    assert points["center"] == [5, 6, 0]


@pytest.mark.parametrize(
    "old,new",
    [
        ("nonce", "stale"),
        ("451", "452"),
        ("test.dwg", "other.dwg"),
        ("\n3\n", "\n4\n"),
        ("\n5\n", "\nnan\n"),
        ("DONE", ""),
    ],
)
def test_bad_evidence_is_rejected(old, new):
    with pytest.raises(ValueError):
        parse(response().replace(old, new))


def test_lisp_literal_rejects_command_newline():
    with pytest.raises(ValueError):
        _lisp_string("filename\n(command)")
    assert _lisp_string('a"b') == '"a\\"b"'


class ReadDocument:
    """Simulate only the read-command exchange, without importing COM."""

    def __init__(self, tmp_path, output="valid"):
        """Create an isolated response writer standing in for AutoCAD."""
        self.FullName = str(tmp_path / "test.dwg")
        self.Application = SimpleNamespace(ActiveDocument=self)
        self.busy = 0
        self.commands = []
        self.output = output

    def GetVariable(self, name):  # noqa: N802 - COM method name
        assert name == "CMDACTIVE"
        return self.busy

    def SendCommand(self, command):  # noqa: N802 - COM method name
        self.commands.append(command)
        if self.output == "missing":
            return
        path = Path(re.search(r'\(open "([^"]+)"', command).group(1))
        nonce = re.search(r'write-line "([0-9a-f]{32})"', command).group(1)
        text = response().replace("nonce", nonce).replace("D:/test.dwg", self.FullName)
        if self.output == "partial":
            text = text.replace("DONE", "")
        elif self.output == "wrong_drawing":
            text = text.replace(self.FullName, str(path.parent / "other.dwg"))
        elif self.output == "switched":
            self.Application.ActiveDocument = SimpleNamespace(FullName="other.dwg")
        elif self.output == "bad_utf8":
            path.write_bytes(b"\xff")
            return
        path.write_text(text, encoding="utf-8")


def read_entity(document):
    return SimpleNamespace(
        Handle="451",
        ObjectName="AcDbDiametricDimension",
        Document=document,
        Layer="AI_PREVIEW_DIM",
        Linetype="Continuous",
        Measurement=15.0,
        TextOverride="",
        TextFill=False,
    )


def test_read_command_returns_bound_points_and_cleans_temporary_output(tmp_path):
    document = ReadDocument(tmp_path)
    actual = dimension_points.read_definition_points(read_entity(document))
    assert actual["far_chord_point"] == [5, 6, 0]
    assert actual["chord_point"] == [-5, -6, 0]
    assert len(document.commands) == 1
    path = Path(re.search(r'\(open "([^"]+)"', document.commands[0]).group(1))
    assert not path.exists()


@pytest.mark.parametrize("invalid", ["busy", "wrong_active", "unsaved", "handle", "kind"])
def test_invalid_read_never_sends_a_command(tmp_path, invalid):
    document = ReadDocument(tmp_path)
    entity = read_entity(document)
    if invalid == "busy":
        document.busy = 1
    elif invalid == "wrong_active":
        document.Application.ActiveDocument = SimpleNamespace(FullName="other.dwg")
    elif invalid == "unsaved":
        document.FullName = "Drawing1.dwg"
    elif invalid == "handle":
        entity.Handle = '451") (command)'
    else:
        entity.ObjectName = "AcDbLine"
    with pytest.raises(ValueError):
        dimension_points.read_definition_points(entity)
    assert document.commands == []


@pytest.mark.parametrize("invalid", [0, -1, float("nan"), float("inf")])
def test_invalid_timeout_never_sends_a_command(tmp_path, invalid):
    document = ReadDocument(tmp_path)
    with pytest.raises(ValueError):
        dimension_points.read_definition_points(read_entity(document), timeout=invalid)
    assert document.commands == []


@pytest.mark.parametrize("output", ["wrong_drawing", "switched"])
def test_changed_drawing_invalidates_readback(tmp_path, output):
    with pytest.raises(ValueError, match="drawing"):
        dimension_points.read_definition_points(read_entity(ReadDocument(tmp_path, output)))


@pytest.mark.parametrize("output", ["missing", "partial", "bad_utf8"])
def test_incomplete_output_times_out_without_busy_waiting(tmp_path, monkeypatch, output):
    clock = iter([0.0, 0.0, 1.0])
    monkeypatch.setattr(dimension_points.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(dimension_points.time, "sleep", lambda _delay: None)
    document = ReadDocument(tmp_path, output)
    with pytest.raises(TimeoutError):
        dimension_points.read_definition_points(read_entity(document), timeout=0.1)
    assert len(document.commands) == 1


def dimension_plan(kind="diametric_dimension", **dimensions):
    coordinates = {"chord_point": [-5, -6, 0]}
    coordinates["far_chord_point" if kind == "diametric_dimension" else "center"] = [5, 6, 0]
    return DrawingPlan.model_validate(
        {
            "task_name": "native dimension",
            "unit": "mm",
            "user_confirmed": True,
            "existing_layers": ["AI_PREVIEW_DIM"],
            "entities": [
                {
                    "entity_type": kind,
                    "coordinates": coordinates,
                    "dimensions": dimensions,
                    "layer": "AI_PREVIEW_DIM",
                    "linetype": "Continuous",
                    "dimension_source": "explicit_dimension",
                    "confidence": 1,
                }
            ],
        }
    )


@pytest.mark.parametrize(
    "kind,field", [("diametric_dimension", "diameter"), ("radial_dimension", "radius")]
)
def test_measurement_checked_without_explicit_measurement(kind, field):
    plan = dimension_plan(kind, **{field: 15})
    rows = PostExecutionVerifier()._compare_entity(0, plan.entities[0], {"measurement": 14}, 0.01)
    row = next(item for item in rows if item["property"] == "measurement")
    assert row["target"] == 15
    assert row["passed"] is False


def test_explicit_measurement_takes_precedence():
    plan = dimension_plan(diameter=15, measurement=16)
    rows = PostExecutionVerifier()._compare_entity(0, plan.entities[0], {"measurement": 16}, 0.01)
    row = next(item for item in rows if item["property"] == "measurement")
    assert row["target"] == 16
    assert row["passed"] is True


@pytest.mark.parametrize("layout_only", [False, True])
def test_missing_native_points_fail_the_actual_verifier(tmp_path, layout_only):
    document = ReadDocument(tmp_path)
    entity = read_entity(document)
    document.HandleToObject = lambda _handle: entity
    adapter = SimpleNamespace(_get_document=lambda _operation: document)
    plan = dimension_plan(diameter=15)
    if layout_only:
        plan.entities[0].operation = "layout_only"
    passed = PostExecutionVerifier().verify(adapter, plan, ["451"])
    assert passed["passed"] is True, passed
    document.busy = 1
    failed = PostExecutionVerifier().verify(adapter, plan, ["451"])
    assert failed["passed"] is False
    assert "definition points unavailable" in str(failed["errors"])
