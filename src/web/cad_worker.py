"""Dedicated STA worker for dashboard CAD commands."""

from __future__ import annotations

import logging
import math
import queue
import threading
import time
from concurrent.futures import Future, InvalidStateError
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from adapters.com_gate import CadOperationGateTimeoutError, cad_operation, next_cad_revision
from core import CADConnectionError

logger = logging.getLogger(__name__)

DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
DEFAULT_QUEUE_SIZE = 32


class CadWorkerError(RuntimeError):
    """Base class for bounded dashboard-worker failures."""

    code = "cad_worker_error"
    cad_revision = 0


class CadWorkerTimeoutError(CadWorkerError):
    """Raised when a queued or running command exceeds its deadline."""

    code = "cad_timeout"

    def __init__(self, message: str, *, outcome_unknown: bool = False) -> None:
        """Initialize a timeout with its running-operation ambiguity flag."""
        super().__init__(message)
        self.outcome_unknown = outcome_unknown


class CadWorkerBusyError(CadWorkerError):
    """Raised when the bounded command queue is full."""

    code = "cad_worker_busy"


class CadWorkerStoppedError(CadWorkerError):
    """Raised when the worker cannot accept more commands."""

    code = "cad_worker_stopped"


class CadDisconnectedError(CadWorkerError):
    """Raised when no running CAD application can be reached."""

    code = "cad_disconnected"


class CadOperationError(CadWorkerError):
    """Raised when a CAD command fails on the worker thread."""

    code = "cad_operation_failed"


def _initialize_sta() -> None:
    """Initialize COM as a single-threaded apartment on the current thread."""
    import pythoncom

    pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)


def _uninitialize_com() -> None:
    """Release the current thread's COM apartment."""
    import pythoncom

    pythoncom.CoUninitialize()


def _default_adapter_provider() -> Any:
    """Resolve a running CAD adapter from the worker's private registry."""
    return _worker_registry().get_adapter(only_if_running=True)


def _default_cad_type_provider() -> str:
    """Return the worker-private registry's active CAD type."""
    return _worker_registry().get_cad_type()


_WORKER_LOCAL = threading.local()


def _worker_registry() -> Any:
    """Create one non-singleton adapter registry owned by the current STA."""
    registry = getattr(_WORKER_LOCAL, "registry", None)
    if registry is None:
        from adapters import AutoCADAdapter
        from adapters.adapter_manager import AdapterRegistry

        registry = AdapterRegistry(
            adapter_factory=lambda cad_type: AutoCADAdapter(
                cad_type,
                manage_com_lifecycle=False,
            )
        )
        _WORKER_LOCAL.registry = registry
    return registry


def _shutdown_worker_registry() -> None:
    """Disconnect the worker-private adapter before COM uninitialization."""
    registry = getattr(_WORKER_LOCAL, "registry", None)
    if registry is not None:
        registry.shutdown_all()
        del _WORKER_LOCAL.registry


def _plain_data(value: Any) -> Any:
    """Copy supported values and reject objects that could hide a live COM proxy."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CadOperationError("CAD command returned a non-finite number")
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise CadOperationError("CAD command returned a mapping with non-string keys")
        return {key: _plain_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_data(item) for item in value]
    raise CadOperationError(
        f"CAD command returned unsupported data type {type(value).__name__}; "
        "live objects cannot cross the worker boundary"
    )


_ENTITY_TYPE_MAP = {
    "L\u00ednea": ["LINE"],
    "Polil\u00ednea": ["LWPOLYLINE", "POLYLINE"],
    "C\u00edrculo": ["CIRCLE"],
    "Arco": ["ARC"],
    "Bloque": ["INSERT"],
    "Texto": ["TEXT", "MTEXT"],
    "Cota": ["DIMENSION"],
    "Spline": ["SPLINE"],
    "Punto": ["POINT"],
    "Sombreado": ["HATCH"],
    "Line": ["LINE"],
    "Polyline": ["LWPOLYLINE", "POLYLINE"],
    "Polyline2D": ["POLYLINE", "LWPOLYLINE"],
    "Circle": ["CIRCLE"],
    "Arc": ["ARC"],
    "Block": ["INSERT"],
    "Text": ["TEXT", "MTEXT"],
    "MText": ["MTEXT", "TEXT"],
    "Dimension": ["DIMENSION"],
    "Point": ["POINT"],
    "Hatch": ["HATCH"],
}


def collect_dashboard_snapshot(
    adapter: Any,
    *,
    cad_type: str,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect one complete plain-data dashboard snapshot on the adapter's thread."""
    fallback_snapshot = fallback or {}
    if hasattr(adapter, "check_document_change"):
        adapter.check_document_change()

    try:
        current_drawing = adapter.document.Name if adapter.document else "None"
    except Exception:
        current_drawing = "None"
    try:
        open_drawings = list(adapter.get_open_drawings())
    except Exception:
        open_drawings = [current_drawing] if current_drawing != "None" else []
    try:
        entity_counts = dict(adapter.get_entity_counts())
        total_entities = sum(int(value) for value in entity_counts.values())
    except Exception as exc:
        logger.warning("Could not get dashboard entity counts: %s", exc)
        entity_counts = {}
        total_entities = 0
    try:
        layers_info = list(adapter.get_layers_info(entity_data=None))
    except Exception as exc:
        logger.warning("Could not get dashboard layers: %s", exc)
        layers_info = list(fallback_snapshot.get("layers", []))
    try:
        insert_counts = dict(adapter.get_block_counts())
    except Exception:
        insert_counts = {}
    try:
        block_names = list(adapter.list_blocks())
    except Exception as exc:
        logger.warning("Could not list dashboard blocks: %s", exc)
        block_names = []

    blocks_info: list[dict[str, Any]] = []
    for name in block_names:
        count = insert_counts.get(name, 0)
        try:
            raw_info = adapter.get_block_info(name)
            info = dict(raw_info) if raw_info else {"Name": name, "ObjectCount": 0}
        except Exception:
            info = {"Name": name, "ObjectCount": 0}
        info["Count"] = count
        blocks_info.append(info)

    try:
        if not adapter.is_connected():
            raise CadDisconnectedError("CAD disconnected during dashboard refresh")
    except CadWorkerError:
        raise
    except Exception as exc:
        raise CadDisconnectedError("CAD disconnected during dashboard refresh") from exc

    return _plain_data(
        {
            "connected": True,
            "cad_type": cad_type or getattr(adapter, "cad_type", "None"),
            "drawings": open_drawings,
            "current_drawing": current_drawing,
            "layers": layers_info,
            "blocks": blocks_info,
            "entities": [],
            "entity_counts": entity_counts,
            "total_entities": total_entities,
        }
    )


class _CadCommands:
    """Execute a closed set of dashboard commands inside the worker apartment."""

    def __init__(
        self,
        adapter_provider: Callable[[], Any],
        cad_type_provider: Callable[[], str],
    ) -> None:
        self._adapter_provider = adapter_provider
        self._cad_type_provider = cad_type_provider

    def execute(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute one named command and return only copied plain data."""
        handlers = {
            "refresh": self._refresh,
            "export": self._export,
            "switch_drawing": self._switch_drawing,
            "entities": self._entities,
        }
        handler = handlers.get(command)
        if handler is None:
            raise CadOperationError(f"Unknown dashboard CAD command: {command}")

        adapter: Any | None = None
        try:
            adapter = self._connected_adapter()
            result = handler(adapter, payload)
        except CadWorkerError:
            raise
        except CADConnectionError as exc:
            raise CadDisconnectedError(str(exc)) from exc
        except Exception as exc:
            try:
                connected = bool(adapter is not None and adapter.is_connected())
            except Exception:
                connected = False
            if not connected:
                raise CadDisconnectedError("CAD disconnected while processing the command") from exc
            raise CadOperationError(str(exc)) from exc
        try:
            if not adapter.is_connected():
                raise CadDisconnectedError("CAD disconnected while processing the command")
        except CadWorkerError:
            raise
        except Exception as exc:
            raise CadDisconnectedError("CAD connection could not be verified") from exc
        result["cad_revision"] = next_cad_revision()
        return _plain_data(result)

    def _connected_adapter(self) -> Any:
        try:
            adapter = self._adapter_provider()
        except CADConnectionError as exc:
            raise CadDisconnectedError(str(exc)) from exc
        except Exception as exc:
            raise CadDisconnectedError(str(exc)) from exc
        if adapter is None:
            raise CadDisconnectedError("No active CAD adapter found")
        try:
            if not adapter.is_connected():
                raise CadDisconnectedError("CAD application is not connected")
        except CadWorkerError:
            raise
        except Exception as exc:
            raise CadDisconnectedError("CAD connection could not be verified") from exc
        return adapter

    def _refresh(self, adapter: Any, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "snapshot": collect_dashboard_snapshot(
                adapter,
                cad_type=self._cad_type_provider(),
                fallback=payload.get("fallback_snapshot", {}),
            )
        }

    def _export(self, adapter: Any, _payload: dict[str, Any]) -> dict[str, Any]:
        success = bool(adapter.export_to_excel())
        if not success:
            raise CadOperationError("CAD export reported failure")
        return {"success": True, "detail": "Export completed"}

    def _switch_drawing(self, adapter: Any, payload: dict[str, Any]) -> dict[str, Any]:
        drawing_name = str(payload.get("drawing_name", "")).strip()
        if not drawing_name:
            raise CadOperationError("drawing_name is required")
        switched = adapter.switch_drawing(drawing_name)
        if switched is False:
            raise CadOperationError(f"CAD did not switch to drawing: {drawing_name}")
        snapshot = collect_dashboard_snapshot(
            adapter,
            cad_type=self._cad_type_provider(),
            fallback=payload.get("fallback_snapshot", {}),
        )
        return {
            "success": True,
            "message": f"Switched to {drawing_name}",
            "snapshot": snapshot,
        }

    def _entities(self, adapter: Any, payload: dict[str, Any]) -> dict[str, Any]:
        page = int(payload.get("page", 1))
        limit = int(payload.get("limit", 500))
        if page < 1:
            raise CadOperationError("page must be at least 1")
        if limit < 1 or limit > 2000:
            raise CadOperationError("limit must be between 1 and 2000")
        requested_name = payload.get("entity_type")
        if hasattr(adapter, "check_document_change"):
            adapter.check_document_change()

        requested_types = _ENTITY_TYPE_MAP.get(str(requested_name)) if requested_name else None
        if requested_name and not requested_types:
            requested_types = [str(requested_name)]

        offset = (page - 1) * limit
        entities: list[Any] = []
        dxf_type: str | None = None
        if requested_types:
            for variant in requested_types:
                found = adapter.extract_drawing_data(
                    only_selected=False,
                    limit=limit,
                    offset=offset,
                    entity_type=variant,
                )
                if found:
                    entities = list(found)
                    dxf_type = variant
                    break
            if not entities:
                dxf_type = requested_types[0]
        else:
            entities = list(
                adapter.extract_drawing_data(
                    only_selected=False,
                    limit=limit,
                    offset=offset,
                )
            )
        try:
            counts = dict(adapter.get_entity_counts())
        except Exception as exc:
            logger.warning("Could not get entity-page totals: %s", exc)
            counts = {}
        if requested_name:
            total_items = int(counts.get(str(requested_name), len(entities)))
        else:
            total_items = sum(int(value) for value in counts.values())
        try:
            current_drawing = adapter.document.Name if adapter.document else "None"
        except Exception:
            current_drawing = "None"
        return {
            "entities": entities,
            "dxf_type": dxf_type,
            "current_drawing": current_drawing,
            "total_items": total_items,
        }


@dataclass
class _Request:
    command: str
    payload: dict[str, Any]
    deadline: float
    future: Future[dict[str, Any]] = field(default_factory=Future)
    cancel_requested: threading.Event = field(default_factory=threading.Event)
    running: threading.Event = field(default_factory=threading.Event)


@dataclass(frozen=True)
class CadWorkerTicket:
    """Handle used by sync or async callers to await and cancel queued work."""

    command: str
    future: Future[dict[str, Any]]
    cancel_requested: threading.Event
    running: threading.Event
    deadline: float

    def cancel(self) -> bool:
        """Cancel the command only if its Future has not started running."""
        self.cancel_requested.set()
        return self.future.cancel()

    @property
    def outcome_unknown(self) -> bool:
        """Return whether a timed-out command had already entered CAD code."""
        return self.running.is_set() and not self.future.done()


class DashboardCadWorker:
    """Serialize dashboard CAD access on one initialized STA thread."""

    def __init__(
        self,
        *,
        adapter_provider: Callable[[], Any] = _default_adapter_provider,
        cad_type_provider: Callable[[], str] = _default_cad_type_provider,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        com_initialize: Callable[[], None] = _initialize_sta,
        com_uninitialize: Callable[[], None] = _uninitialize_com,
        worker_cleanup: Callable[[], None] = _shutdown_worker_registry,
    ) -> None:
        """Configure a lazy, bounded worker without starting its COM thread."""
        if request_timeout <= 0:
            raise ValueError("request_timeout must be positive")
        if queue_size <= 0:
            raise ValueError("queue_size must be positive")
        self._commands = _CadCommands(adapter_provider, cad_type_provider)
        self._request_timeout = float(request_timeout)
        self._queue: queue.Queue[_Request] = queue.Queue(maxsize=queue_size)
        self._com_initialize = com_initialize
        self._com_uninitialize = com_uninitialize
        self._worker_cleanup = worker_cleanup
        self._state_lock = threading.Lock()
        self._ready = threading.Event()
        self._stop_requested = threading.Event()
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._startup_error: str | None = None
        self._closed = False
        self._active_request: _Request | None = None

    @property
    def thread_id(self) -> int | None:
        """Return the worker thread identifier after startup."""
        return self._thread_id

    def status(self) -> dict[str, Any]:
        """Return worker lifecycle diagnostics without touching CAD or COM."""
        with self._state_lock:
            thread = self._thread
            active = self._active_request
            return {
                "started": thread is not None,
                "ready": self._ready.is_set() and self._startup_error is None,
                "alive": bool(thread and thread.is_alive()),
                "closing": self._closed,
                "thread_id": self._thread_id,
                "queue_depth": self._queue.qsize(),
                "active_command": active.command if active is not None else None,
                "startup_error": self._startup_error,
            }

    def request(
        self,
        command: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Queue one command and wait for its bounded plain-data result."""
        if threading.get_ident() == self._thread_id:
            raise CadWorkerError("A worker command cannot synchronously queue another command")
        wait_timeout = self._request_timeout if timeout is None else float(timeout)
        if wait_timeout <= 0:
            raise ValueError("timeout must be positive")
        ticket = self.submit(command, payload, timeout=wait_timeout)
        remaining = max(0.0, ticket.deadline - time.monotonic())
        try:
            return ticket.future.result(timeout=remaining)
        except FutureTimeoutError as exc:
            ticket.cancel_requested.set()
            cancelled = ticket.future.cancel()
            outcome_unknown = ticket.running.is_set() and not cancelled
            raise CadWorkerTimeoutError(
                f"Dashboard CAD command '{command}' timed out after {wait_timeout:g}s",
                outcome_unknown=outcome_unknown,
            ) from exc

    def submit(
        self,
        command: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> CadWorkerTicket:
        """Queue a command and return a non-blocking ticket for async callers."""
        if threading.get_ident() == self._thread_id:
            raise CadWorkerError("A worker command cannot queue another command")
        wait_timeout = self._request_timeout if timeout is None else float(timeout)
        if wait_timeout <= 0:
            raise ValueError("timeout must be positive")
        deadline = time.monotonic() + wait_timeout
        request = _Request(
            command=command,
            payload=_plain_data(payload or {}),
            deadline=deadline,
        )
        with self._state_lock:
            if self._closed or self._stop_requested.is_set():
                raise CadWorkerStoppedError("Dashboard CAD worker is stopped")
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run,
                    name="multiCAD-dashboard-STA",
                    daemon=True,
                )
                self._thread.start()
            elif not self._thread.is_alive() and not self._ready.is_set():
                self._closed = True
                raise CadWorkerStoppedError("Dashboard CAD worker exited during startup")
            if self._startup_error is not None:
                raise CadWorkerStoppedError(
                    f"Dashboard CAD worker failed to initialize: {self._startup_error}"
                )
            try:
                self._queue.put_nowait(request)
            except queue.Full as exc:
                raise CadWorkerBusyError("Dashboard CAD command queue is full") from exc
        return CadWorkerTicket(
            command=command,
            future=request.future,
            cancel_requested=request.cancel_requested,
            running=request.running,
            deadline=deadline,
        )

    def shutdown(self, timeout: float = 2.0) -> bool:
        """Stop accepting work and wait briefly for the STA thread to finish."""
        with self._state_lock:
            self._closed = True
            thread = self._thread
            self._stop_requested.set()
            pending = self._take_pending_requests()
        self._finish_requests_as_stopped(pending)
        if thread is None:
            return True
        if thread is threading.current_thread():
            return False
        thread.join(max(0.0, float(timeout)))
        return not thread.is_alive()

    def _run(self) -> None:
        initialized = False
        self._thread_id = threading.get_ident()
        try:
            self._com_initialize()
            initialized = True
        except BaseException as exc:
            with self._state_lock:
                self._startup_error = str(exc)
                self._closed = True
                self._stop_requested.set()
                self._ready.set()
                pending = self._take_pending_requests()
            self._finish_requests_as_stopped(pending)
            return
        self._ready.set()
        try:
            while not self._stop_requested.is_set():
                try:
                    request = self._queue.get(timeout=0.1)
                except queue.Empty:
                    self._pump_messages()
                    continue
                try:
                    self._execute_request(request)
                finally:
                    self._queue.task_done()
                    self._active_request = None
                    self._pump_messages()
        finally:
            with self._state_lock:
                self._closed = True
                self._stop_requested.set()
                pending = self._take_pending_requests()
            self._finish_requests_as_stopped(pending)
            if initialized:
                try:
                    self._worker_cleanup()
                except Exception as exc:
                    logger.debug("Dashboard worker adapter cleanup failed: %s", exc)
                try:
                    self._com_uninitialize()
                except Exception as exc:
                    logger.debug("Dashboard worker CoUninitialize failed: %s", exc)

    def _execute_request(self, request: _Request) -> None:
        if (
            self._stop_requested.is_set()
            or request.cancel_requested.is_set()
            or time.monotonic() >= request.deadline
        ):
            error: CadWorkerError
            if self._stop_requested.is_set():
                error = CadWorkerStoppedError("Dashboard CAD worker stopped before execution")
            else:
                error = CadWorkerTimeoutError(
                    f"Dashboard CAD command '{request.command}' expired before execution"
                )
            self._set_exception_if_pending(request.future, error)
            return
        if not request.future.set_running_or_notify_cancel():
            return
        self._active_request = request
        try:
            remaining = max(0.0, request.deadline - time.monotonic())
            with cad_operation(timeout=remaining):
                if (
                    self._stop_requested.is_set()
                    or request.cancel_requested.is_set()
                    or time.monotonic() >= request.deadline
                ):
                    if self._stop_requested.is_set():
                        raise CadWorkerStoppedError("Dashboard CAD worker stopped before execution")
                    raise CadWorkerTimeoutError(
                        f"Dashboard CAD command '{request.command}' expired before execution"
                    )
                request.running.set()
                try:
                    result = self._commands.execute(request.command, request.payload)
                except BaseException as exc:
                    error = self._sanitized_error(exc)
                    error.cad_revision = next_cad_revision()
                    raise error
        except CadOperationGateTimeoutError:
            self._set_exception_if_pending(
                request.future,
                CadWorkerTimeoutError("Timed out waiting for another CAD operation"),
            )
        except BaseException as exc:
            self._set_exception_if_pending(request.future, self._sanitized_error(exc))
        else:
            self._set_result_if_pending(request.future, result)

    @staticmethod
    def _sanitized_error(exc: BaseException) -> CadWorkerError:
        """Copy only stable error data so worker traceback frames do not cross threads."""
        if isinstance(exc, CadDisconnectedError):
            error: CadWorkerError = CadDisconnectedError(str(exc))
        elif isinstance(exc, CadWorkerTimeoutError):
            error = CadWorkerTimeoutError(
                str(exc),
                outcome_unknown=exc.outcome_unknown,
            )
        elif isinstance(exc, CadWorkerError):
            error = type(exc)(str(exc))
        else:
            error = CadOperationError(str(exc))
        error.cad_revision = int(getattr(exc, "cad_revision", 0))
        return error

    @staticmethod
    def _pump_messages() -> None:
        """Let the STA dispatch pending COM messages between serialized commands."""
        try:
            import pythoncom

            pythoncom.PumpWaitingMessages()
        except Exception as exc:
            logger.debug("Dashboard worker COM message pump failed: %s", exc)

    def _take_pending_requests(self) -> list[_Request]:
        pending: list[_Request] = []
        while True:
            try:
                request = self._queue.get_nowait()
            except queue.Empty:
                return pending
            pending.append(request)
            self._queue.task_done()

    @classmethod
    def _finish_requests_as_stopped(cls, requests: list[_Request]) -> None:
        for request in requests:
            cls._set_exception_if_pending(
                request.future,
                CadWorkerStoppedError("Dashboard CAD worker stopped before execution"),
            )

    @staticmethod
    def _set_exception_if_pending(future: Future[Any], error: BaseException) -> bool:
        try:
            future.set_exception(error)
        except InvalidStateError:
            return False
        return True

    @staticmethod
    def _set_result_if_pending(future: Future[Any], result: Any) -> bool:
        try:
            future.set_result(result)
        except InvalidStateError:
            return False
        return True
