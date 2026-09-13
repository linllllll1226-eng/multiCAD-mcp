"""Read native radial/diametric definition points absent from AutoCAD ActiveX.

The fixed AutoLISP expression only reads entget and writes a nonce-bound temporary
response. It does not edit entities, system variables, selection, or save state.
DXF group 15 is the chord; group 10 is the opposite chord or radial center.
"""

from __future__ import annotations

import math
import re
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4


def _lisp_string(value: str) -> str:
    """Quote a literal without allowing commands or line breaks in it."""
    if any(ord(char) < 32 for char in value):
        raise ValueError("Control character in AutoLISP literal")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse_definition_points(
    text: str, *, nonce: str, handle: str, drawing: str, object_type: str
) -> dict[str, Any]:
    """Reject stale, wrong-drawing, wrong-handle, partial, or nonfinite output."""
    lines = text.strip().splitlines()
    if len(lines) != 11 or lines[0] != nonce or lines[-1] != "DONE":
        raise ValueError("Incomplete or stale dimension-point response")
    if lines[1].casefold() != handle.casefold():
        raise ValueError("Dimension-point response belongs to another handle")
    if lines[2].replace("\\", "/").casefold() != drawing.replace("\\", "/").casefold():
        raise ValueError("Dimension-point response belongs to another drawing")
    expected_type = {"AcDbDiametricDimension": 3, "AcDbRadialDimension": 4}[object_type]
    if int(lines[3]) != expected_type:
        raise ValueError("Dimension-point response has the wrong dimension type")
    values = [float(value) for value in lines[4:10]]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Nonfinite dimension definition point")
    points: dict[str, Any] = {"chord_point": values[3:]}
    points["far_chord_point" if expected_type == 3 else "center"] = values[:3]
    points["definition_points_source"] = "live_entget_dxf_10_15"
    return points


def read_definition_points(entity: Any, *, timeout: float = 3.0) -> dict[str, Any]:
    """Bound response polling; AutoCAD's SendCommand call itself is not cancellable."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Polling timeout must be finite and positive")
    handle = str(entity.Handle)
    kind = str(entity.ObjectName)
    if not re.fullmatch(r"[0-9A-Fa-f]+", handle):
        raise ValueError("Invalid CAD entity handle")
    if kind not in {"AcDbDiametricDimension", "AcDbRadialDimension"}:
        raise ValueError("Only native radial and diametric dimensions are supported")
    document = entity.Document
    drawing = str(document.FullName)
    if (
        not Path(drawing).is_absolute()
        or str(document.Application.ActiveDocument.FullName) != drawing
    ):
        raise ValueError("Dimension document is not the active saved drawing")
    if int(document.GetVariable("CMDACTIVE")) != 0:
        raise ValueError("AutoCAD is busy with another command; no command was sent")
    nonce = uuid4().hex
    with tempfile.TemporaryDirectory(prefix="multicad-read-") as directory:
        result_path = Path(directory) / "points.txt"
        output_literal = _lisp_string(result_path.as_posix())
        command = (
            "((lambda (e f n) "
            '(if (and e (= (cdr (assoc 0 e)) "DIMENSION")) '
            "(progn "
            f'(setq f (open {output_literal} "w" "utf8")) '
            f'(write-line "{nonce}" f) '
            "(write-line (cdr (assoc 5 e)) f) "
            '(write-line (strcat (getvar "DWGPREFIX") (getvar "DWGNAME")) f) '
            "(write-line (itoa (logand 7 (cdr (assoc 70 e)))) f) "
            "(foreach n (append (cdr (assoc 10 e)) (cdr (assoc 15 e))) "
            "(write-line (rtos n 2 16) f)) "
            '(write-line "DONE" f) (close f))) (princ)) '
            f'(entget (handent "{handle}")) nil nil)\n'
        )
        document.SendCommand(command)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if result_path.is_file():
                try:
                    text = result_path.read_text(encoding="utf-8-sig")
                except (OSError, UnicodeDecodeError):
                    # AutoCAD may still hold the output or be writing a UTF-8 character.
                    time.sleep(0.02)
                    continue
                if text.rstrip().endswith("DONE"):
                    if str(document.Application.ActiveDocument.FullName) != drawing:
                        raise ValueError("Active drawing changed during dimension read")
                    return parse_definition_points(
                        text, nonce=nonce, handle=handle, drawing=drawing, object_type=kind
                    )
            time.sleep(0.02)
    raise TimeoutError("AutoCAD did not return a complete dimension-point response")
