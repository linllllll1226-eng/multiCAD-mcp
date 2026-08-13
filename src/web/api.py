"""FastAPI endpoints and thread-safe cache for the optional web dashboard."""

import asyncio
import collections
import logging
import threading
import time
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from __version__ import __version__
from core import get_supported_cads
from web.cad_worker import (
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    CadDisconnectedError,
    CadOperationError,
    CadWorkerBusyError,
    CadWorkerStoppedError,
    CadWorkerTicket,
    CadWorkerTimeoutError,
    DashboardCadWorker,
    collect_dashboard_snapshot,
)

logger = logging.getLogger(__name__)


# ---------- In-memory log buffer for dashboard console ----------


class _LogBuffer:
    """Thread-safe circular buffer for log records."""

    def __init__(self, maxlen: int = 500):
        self._lock = threading.Lock()
        self._entries: collections.deque = collections.deque(maxlen=maxlen)
        self._seq = 0

    def append(self, level: str, name: str, msg: str, time_str: str) -> None:
        with self._lock:
            self._seq += 1
            self._entries.append(
                {
                    "seq": self._seq,
                    "time": time_str,
                    "level": level,
                    "name": name,
                    "msg": msg,
                }
            )

    def since(self, seq: int) -> list:
        with self._lock:
            return [e for e in self._entries if e["seq"] > seq]


class _MemoryLogHandler(logging.Handler):
    """Logging handler that stores records in _LogBuffer."""

    def __init__(self, buffer: _LogBuffer):
        """Attach the handler to an in-memory log buffer."""
        super().__init__()
        self._buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            from datetime import datetime

            time_str = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
            self._buffer.append(
                level=record.levelname,
                name=record.name,
                msg=record.getMessage(),
                time_str=time_str,
            )
        except Exception:
            self.handleError(record)


log_buffer = _LogBuffer(maxlen=500)
log_handler = _MemoryLogHandler(log_buffer)
log_handler.setLevel(logging.INFO)


_cad_worker = DashboardCadWorker()
_cad_worker_lifecycle_lock = threading.Lock()


def _prepare_dashboard_worker() -> DashboardCadWorker:
    """Prepare one worker at lifespan startup without duplicating a stuck thread."""
    global _cad_worker
    with _cad_worker_lifecycle_lock:
        status = _cad_worker.status()
        if status["closing"] and status["alive"]:
            raise RuntimeError("The previous dashboard CAD worker is still stopping")
        if status["closing"]:
            _cad_worker = DashboardCadWorker()
        return _cad_worker


@asynccontextmanager
async def _api_lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Release the dashboard worker when the HTTP application stops."""
    _prepare_dashboard_worker()
    try:
        yield
    finally:
        stopped = await asyncio.to_thread(
            shutdown_dashboard_worker,
            DEFAULT_REQUEST_TIMEOUT_SECONDS + 1.0,
        )
        if not stopped:
            logger.error(
                "Dashboard CAD worker did not stop before the %.1fs shutdown deadline",
                DEFAULT_REQUEST_TIMEOUT_SECONDS + 1.0,
            )


# FastAPI App
api_app = FastAPI(title="multiCAD-MCP Dashboard API", lifespan=_api_lifespan)


class DashboardCadHttpError(Exception):
    """HTTP-safe wrapper for a sanitized dashboard worker error."""

    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        """Store an explicit status and front-end-compatible JSON payload."""
        super().__init__(payload["message"])
        self.status_code = status_code
        self.payload = payload


@api_app.exception_handler(DashboardCadHttpError)
async def _dashboard_cad_error_handler(
    _request: Request,
    exc: DashboardCadHttpError,
) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.payload)


def _worker_error_response(exc: Exception) -> DashboardCadHttpError:
    """Map bounded worker failures to explicit, stable HTTP error details."""
    if isinstance(exc, CadWorkerTimeoutError):
        status_code = 504
    elif isinstance(
        exc,
        (CadDisconnectedError, CadWorkerBusyError, CadWorkerStoppedError),
    ):
        status_code = 503
    else:
        status_code = 502
    return DashboardCadHttpError(
        status_code,
        {
            "success": False,
            "error_code": getattr(exc, "code", "cad_operation_failed"),
            "detail": str(exc),
            "message": str(exc),
            "outcome_unknown": bool(getattr(exc, "outcome_unknown", False)),
        },
    )


async def _await_ticket(ticket: CadWorkerTicket) -> dict[str, Any]:
    """Await a worker Future without blocking the ASGI event loop."""
    remaining = max(0.0, ticket.deadline - time.monotonic())
    wrapped = asyncio.wrap_future(ticket.future)
    try:
        return await asyncio.wait_for(asyncio.shield(wrapped), timeout=remaining)
    except asyncio.TimeoutError as exc:
        cancelled = ticket.cancel()
        raise CadWorkerTimeoutError(
            f"Dashboard CAD command '{ticket.command}' timed out",
            outcome_unknown=ticket.running.is_set() and not cancelled,
        ) from exc
    except asyncio.CancelledError:
        ticket.cancel()
        raise


async def _request_cad(
    command: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Submit one command and translate worker failures for FastAPI."""
    try:
        ticket = _cad_worker.submit(command, payload)
        return await _await_ticket(ticket)
    except (
        CadDisconnectedError,
        CadOperationError,
        CadWorkerBusyError,
        CadWorkerStoppedError,
        CadWorkerTimeoutError,
    ) as exc:
        logger.warning("Dashboard CAD command %s failed: %s", command, exc)
        if isinstance(exc, CadDisconnectedError):
            _cache.mark_disconnected(cad_revision=exc.cad_revision)
        raise _worker_error_response(exc) from exc


@api_app.post("/api/cad/export")
async def api_cad_export() -> dict:
    """Run an Excel export inside the dedicated CAD apartment."""
    result = await _request_cad("export")
    result.pop("cad_revision", None)
    return result


@api_app.post("/api/cad/refresh")
async def api_cad_trigger_refresh() -> dict:
    """Refresh the immutable dashboard snapshot through the CAD worker."""
    result = await _request_cad(
        "refresh",
        {"fallback_snapshot": _cache.snapshot()},
    )
    snapshot = result["snapshot"]
    snapshot["cad_revision"] = result["cad_revision"]
    _cache.replace(snapshot)
    return {
        "success": True,
        "detail": "Refresh completed",
        "connected": snapshot["connected"],
    }


# ---------- Thread-safe dashboard cache ----------
# COM objects cannot be accessed cross-thread on Windows (STA threading).
# The MCP thread calls refresh_dashboard_cache() after connecting or
# performing operations, and the dashboard thread just reads the cache.


class DashboardCache:
    """Thread-safe store of immutable, plain-data dashboard snapshots."""

    def __init__(self):
        """Initialize an empty dashboard snapshot and its synchronization lock."""
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = self._empty_snapshot()

    @staticmethod
    def _empty_snapshot() -> Dict[str, Any]:
        return {
            "connected": False,
            "cad_type": "None",
            "drawings": [],
            "current_drawing": "None",
            "layers": [],
            "blocks": [],
            "entities": [],
            "entity_counts": {},
            "total_entities": 0,
            "generation": 0,
            "cad_revision": 0,
            "refreshed_at": None,
        }

    def replace(self, snapshot: Dict[str, Any]) -> bool:
        """Publish a complete snapshot unless a newer CAD result already won."""
        with self._lock:
            incoming_revision = int(snapshot.get("cad_revision", 0))
            current_revision = int(self._data.get("cad_revision", 0))
            if current_revision and (not incoming_revision or incoming_revision < current_revision):
                return False
            generation = int(self._data.get("generation", 0)) + 1
            replacement = self._empty_snapshot()
            replacement.update(deepcopy(snapshot))
            replacement["cad_revision"] = max(incoming_revision, current_revision)
            replacement["generation"] = generation
            replacement["refreshed_at"] = time.time()
            self._data = replacement
            return True

    def update(self, **kwargs: Any) -> None:
        """Atomically update selected fields while retaining compatibility."""
        with self._lock:
            replacement = deepcopy(self._data)
            replacement.update(deepcopy(kwargs))
            replacement["generation"] = int(self._data.get("generation", 0)) + 1
            replacement["refreshed_at"] = time.time()
            self._data = replacement

    def mark_disconnected(self, *, cad_revision: int = 0) -> bool:
        """Clear stale drawing data after a confirmed worker disconnect."""
        snapshot = self._empty_snapshot()
        snapshot["cad_revision"] = cad_revision
        return self.replace(snapshot)

    def get(self, key: str, default: Any = None) -> Any:
        """Return a deep copy so nested cache data cannot be mutated by callers."""
        with self._lock:
            return deepcopy(self._data.get(key, default))

    def snapshot(self) -> Dict[str, Any]:
        """Get one generation-consistent deep copy of the cache."""
        with self._lock:
            return deepcopy(self._data)


_cache = DashboardCache()


def mark_dashboard_disconnected() -> None:
    """Clear dashboard state without resolving or probing a CAD adapter."""
    from adapters.com_gate import next_cad_revision

    _cache.mark_disconnected(cad_revision=next_cad_revision())


def shutdown_dashboard_worker(
    timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS + 1.0,
) -> bool:
    """Stop the process-local dashboard STA worker."""
    return _cad_worker.shutdown(timeout=timeout)


def refresh_dashboard_cache() -> Dict[str, Any]:
    """Refresh cache on an existing CAD owner thread, normally an MCP call.

    HTTP routes must use the dedicated worker instead. This compatibility entry
    point never passes the adapter outside the thread that resolved it.
    """
    from adapters.adapter_manager import get_active_cad_type, get_adapter
    from adapters.com_gate import (
        DEFAULT_CAD_GATE_TIMEOUT_SECONDS,
        cad_operation,
        next_cad_revision,
    )

    with cad_operation(timeout=DEFAULT_CAD_GATE_TIMEOUT_SECONDS):
        try:
            adapter = get_adapter(only_if_running=True)
            snapshot = collect_dashboard_snapshot(
                adapter,
                cad_type=get_active_cad_type(),
                fallback=_cache.snapshot(),
            )
            snapshot["cad_revision"] = next_cad_revision()
        except Exception:
            _cache.mark_disconnected(cad_revision=next_cad_revision())
            raise
    _cache.replace(snapshot)
    return _cache.snapshot()


# ---------- Static files ----------
STATIC_DIR = Path(__file__).parent / "static"


class ProjectState:
    """Project state tracking."""

    def __init__(self):
        """Initialize dashboard refresh state."""
        self.last_refresh = None


state = ProjectState()


class SwitchDrawingRequest(BaseModel):
    """Request payload for selecting an open drawing by name."""

    drawing_name: str


@api_app.get("/")
async def get_index() -> FileResponse:
    """Serve the main dashboard page."""
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend not found")
    return FileResponse(index_path)


@api_app.get("/api/health")
async def api_health() -> dict:
    """Health check endpoint."""
    return {"status": "ok", "version": __version__}


@api_app.get("/api/debug/registry")
async def api_debug_registry() -> dict:
    """Return cache and worker diagnostics without probing a live COM proxy."""
    snapshot = _cache.snapshot()
    return {
        "active_cad_type": snapshot["cad_type"],
        "instances": [snapshot["cad_type"]] if snapshot["connected"] else [],
        "cache": snapshot,
        "worker": _cad_worker.status(),
    }


@api_app.post("/api/cad/switch_drawing")
async def api_cad_switch_drawing(request: SwitchDrawingRequest) -> dict:
    """Switch the active CAD drawing and trigger a cache refresh."""
    result = await _request_cad(
        "switch_drawing",
        {
            "drawing_name": request.drawing_name,
            "fallback_snapshot": _cache.snapshot(),
        },
    )
    snapshot = result.pop("snapshot")
    snapshot["cad_revision"] = result.pop("cad_revision")
    _cache.replace(snapshot)
    return result


@api_app.get("/api/cad/status")
async def api_cad_status() -> dict:
    """Get current CAD connection status (from cache)."""
    snapshot = _cache.snapshot()
    return {
        "success": True,
        "status": {
            "connected": snapshot["connected"],
            "cad_type": snapshot["cad_type"],
            "drawings": snapshot["drawings"],
            "current_drawing": snapshot["current_drawing"],
            "supported": get_supported_cads(),
            "total_entities": snapshot["total_entities"],
            "entity_counts": snapshot["entity_counts"],
            "generation": snapshot["generation"],
            "cad_revision": snapshot["cad_revision"],
            "refreshed_at": snapshot["refreshed_at"],
        },
    }


@api_app.get("/api/cad/layers")
async def api_cad_layers() -> dict:
    """Get layers from the active CAD drawing (from cache)."""
    snapshot = _cache.snapshot()
    if not snapshot["connected"]:
        return {"success": False, "error": "No CAD connection"}
    return {"success": True, "layers": snapshot["layers"]}


@api_app.get("/api/cad/blocks")
async def api_cad_blocks() -> dict:
    """Get block definitions from the active CAD drawing (from cache)."""
    snapshot = _cache.snapshot()
    if not snapshot["connected"]:
        return {"success": False, "error": "No CAD connection"}
    return {"success": True, "blocks": snapshot["blocks"]}


@api_app.get("/api/cad/entities")
async def api_cad_entities(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=500, ge=1, le=2000),
    type: Optional[str] = Query(default=None),
) -> dict:
    """Get entities from the active CAD drawing dynamically (paginated, by type)."""
    result = await _request_cad(
        "entities",
        {"page": page, "limit": limit, "entity_type": type},
    )
    snapshot = _cache.snapshot()
    total_items = int(result["total_items"])
    document_changed = result.get("current_drawing") != snapshot["current_drawing"]
    return {
        "success": True,
        "entities": result["entities"],
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total_items,
            "total_pages": (total_items + limit - 1) // limit if total_items > 0 else 1,
            "type": type,
            "dxf_type": result["dxf_type"],
            "cache_generation": snapshot["generation"],
            "cad_revision": result["cad_revision"],
            "cache_stale": document_changed,
        },
    }


@api_app.get("/api/cad/drawings")
async def api_cad_drawings() -> dict:
    """Get summary of all open drawings (from cache)."""
    snapshot = _cache.snapshot()
    if not snapshot["connected"]:
        return {"success": False, "error": "No CAD connection"}

    drawing_names = snapshot["drawings"]
    current = snapshot["current_drawing"]

    drawings_info = []
    for name in drawing_names:
        drawings_info.append(
            {
                "name": name,
                "is_active": name == current,
            }
        )

    return {"success": True, "drawings": drawings_info, "current": current}


@api_app.get("/api/logs")
async def api_logs(since: int = Query(default=0, ge=0)) -> dict:
    """Get server log entries newer than the given sequence number."""
    return {"success": True, "entries": log_buffer.since(since)}


# Mount static files (at the end to not shadow API routes)
if STATIC_DIR.exists():
    api_app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
else:
    logger.warning(f"Static directory not found: {STATIC_DIR}")
