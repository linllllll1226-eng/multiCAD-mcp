"""Process-wide serialization gate for CAD COM operations."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

_CAD_OPERATION_GATE = threading.RLock()
_CAD_REVISION_LOCK = threading.Lock()
_CAD_REVISION = 0

DEFAULT_CAD_GATE_TIMEOUT_SECONDS = 30.0


class CadOperationGateTimeoutError(TimeoutError):
    """Raised when a bounded caller cannot enter the CAD operation gate."""


@contextmanager
def cad_operation(timeout: float | None = None) -> Iterator[None]:
    """Serialize one complete CAD operation across MCP and dashboard threads."""
    if timeout is None:
        acquired = _CAD_OPERATION_GATE.acquire()
    else:
        acquired = _CAD_OPERATION_GATE.acquire(timeout=max(0.0, float(timeout)))
    if not acquired:
        raise CadOperationGateTimeoutError("Timed out waiting for another CAD operation")
    try:
        yield
    finally:
        _CAD_OPERATION_GATE.release()


def next_cad_revision() -> int:
    """Return a process-local monotonic revision for one serialized CAD result."""
    global _CAD_REVISION
    with _CAD_REVISION_LOCK:
        _CAD_REVISION += 1
        return _CAD_REVISION
