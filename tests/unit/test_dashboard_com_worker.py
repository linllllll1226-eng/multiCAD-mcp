"""Integration coverage for dashboard HTTP routes and the dedicated CAD worker."""

import asyncio
import sys
import threading
import time
from concurrent.futures import CancelledError
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from adapters.com_gate import cad_operation
from web import api as dashboard_api
from web.cad_worker import (
    CadWorkerStoppedError,
    DashboardCadWorker,
)

pytestmark = pytest.mark.integration


class _WorkerHarness:
    """Record lifecycle and fake CAD calls without constructing CAD outside the worker."""

    def __init__(self, *, block_export: bool = False) -> None:
        self._lock = threading.Lock()
        self._calls: list[tuple[str, int, dict[str, Any]]] = []
        self.adapter: _ThreadBoundFakeAdapter | None = None
        self.owner_thread_id: int | None = None
        self.connected = True
        self.export_entered = threading.Event()
        self.export_finished = threading.Event()
        self.export_release = threading.Event()
        self.real_com_initialized = threading.Event()
        if not block_export:
            self.export_release.set()

    def record(self, name: str, **details: Any) -> None:
        """Append one thread-stamped call record."""
        with self._lock:
            self._calls.append((name, threading.get_ident(), details))

    def calls(self) -> list[tuple[str, int, dict[str, Any]]]:
        """Return a stable copy of all recorded calls."""
        with self._lock:
            return list(self._calls)

    def names(self) -> list[str]:
        """Return recorded call names in execution order."""
        return [name for name, _thread_id, _details in self.calls()]

    def count(self, name: str) -> int:
        """Count records with an exact call name."""
        return sum(call_name == name for call_name, _thread_id, _details in self.calls())

    def adapter_call_count(self) -> int:
        """Count all fake-adapter method and property calls."""
        return sum(name.startswith("adapter:") for name, _thread_id, _details in self.calls())

    def adapter_provider(self) -> "_ThreadBoundFakeAdapter":
        """Create the fake adapter lazily on the STA worker thread."""
        self.record("provider")
        if self.adapter is None:
            self.adapter = _ThreadBoundFakeAdapter(self)
            self.owner_thread_id = self.adapter.owner_thread_id
        assert self.adapter.owner_thread_id == threading.get_ident()
        return self.adapter

    def cad_type_provider(self) -> str:
        """Return a plain CAD type from the worker thread."""
        self.record("cad_type")
        return "autocad"

    def com_initialize(self) -> None:
        """Record the worker's COM initialization hook."""
        self.record("com:init")

    def com_uninitialize(self) -> None:
        """Record the worker's COM uninitialization hook."""
        self.record("com:uninit")

    def cleanup(self) -> None:
        """Disconnect the fake adapter before COM uninitialization."""
        self.record("cleanup")
        if self.adapter is not None:
            self.adapter.disconnect()

    def make_worker(
        self,
        *,
        request_timeout: float = 1.0,
        queue_size: int = 4,
        real_com_apartment: bool = False,
    ) -> DashboardCadWorker:
        """Build a real worker with observable fake CAD and COM boundaries."""
        com_initialize = self.com_initialize
        com_uninitialize = self.com_uninitialize
        if real_com_apartment:

            def com_initialize() -> None:
                import pythoncom

                pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
                self.real_com_initialized.set()
                self.record("com:init")

            def com_uninitialize() -> None:
                import pythoncom

                self.record("com:uninit")
                pythoncom.CoUninitialize()

        return DashboardCadWorker(
            adapter_provider=self.adapter_provider,
            cad_type_provider=self.cad_type_provider,
            request_timeout=request_timeout,
            queue_size=queue_size,
            com_initialize=com_initialize,
            com_uninitialize=com_uninitialize,
            worker_cleanup=self.cleanup,
        )


class _FakeDocument:
    """Thread-bound stand-in for the small document surface used by the dashboard."""

    def __init__(self, harness: _WorkerHarness, name: str) -> None:
        self._harness = harness
        self._name = name

    @property
    def Name(self) -> str:  # noqa: N802 - mirrors the AutoCAD COM property
        """Return the drawing name only on the adapter owner thread."""
        self._harness.adapter._touch("document.Name")  # type: ignore[union-attr]
        return self._name


class _ThreadBoundFakeAdapter:
    """Fail immediately if any dashboard CAD access leaves its owner thread."""

    cad_type = "autocad"

    def __init__(self, harness: _WorkerHarness) -> None:
        self._harness = harness
        self.owner_thread_id = threading.get_ident()
        self._document = _FakeDocument(harness, "A.dwg")

    def _touch(self, method: str, **details: Any) -> None:
        assert threading.get_ident() == self.owner_thread_id, (
            f"fake CAD method {method} escaped the worker thread"
        )
        self._harness.record(f"adapter:{method}", **details)

    @property
    def document(self) -> _FakeDocument:
        """Return the thread-bound fake document."""
        self._touch("document")
        return self._document

    def is_connected(self) -> bool:
        """Return the configurable fake connection state."""
        self._touch("is_connected")
        return self._harness.connected

    def check_document_change(self) -> bool:
        """Report no external active-document change."""
        self._touch("check_document_change")
        return False

    def get_open_drawings(self) -> list[str]:
        """Return two deterministic drawing names."""
        self._touch("get_open_drawings")
        return ["A.dwg", "B.dwg"]

    def get_entity_counts(self) -> dict[str, int]:
        """Return deterministic cache totals."""
        self._touch("get_entity_counts")
        return {"Line": 3, "Circle": 1}

    def get_layers_info(self, entity_data: Any = None) -> list[dict[str, Any]]:
        """Return one plain-data layer record."""
        self._touch("get_layers_info", entity_data=entity_data)
        return [{"Name": "0", "Count": 4}]

    def get_block_counts(self) -> dict[str, int]:
        """Return one deterministic block insertion count."""
        self._touch("get_block_counts")
        return {"BlockA": 1}

    def list_blocks(self) -> list[str]:
        """Return one deterministic block name."""
        self._touch("list_blocks")
        return ["BlockA"]

    def get_block_info(self, name: str) -> dict[str, Any]:
        """Return plain block metadata."""
        self._touch("get_block_info", name=name)
        return {"Name": name, "ObjectCount": 2}

    def export_to_excel(self) -> bool:
        """Optionally block export so timeout and responsiveness are deterministic."""
        self._touch("export_to_excel")
        self._harness.export_entered.set()
        if not self._harness.export_release.wait(timeout=2.0):
            raise AssertionError("test did not release the fake export")
        self._harness.export_finished.set()
        return True

    def switch_drawing(self, drawing_name: str) -> bool:
        """Switch the fake active document."""
        self._touch("switch_drawing", drawing_name=drawing_name)
        self._document._name = drawing_name
        return True

    def extract_drawing_data(self, **kwargs: Any) -> list[dict[str, Any]]:
        """Return one plain entity and record exact pagination/filter arguments."""
        self._touch("extract_drawing_data", **kwargs)
        return [{"Handle": "3", "Type": "Line", "Layer": "0"}]

    def disconnect(self) -> bool:
        """Record worker-owned adapter cleanup."""
        self._touch("disconnect")
        self._harness.connected = False
        return True


def _install_dashboard(
    monkeypatch: pytest.MonkeyPatch,
    harness: _WorkerHarness,
    *,
    request_timeout: float = 1.0,
    queue_size: int = 4,
    real_com_apartment: bool = False,
) -> tuple[DashboardCadWorker, dashboard_api.DashboardCache]:
    """Install isolated worker/cache globals used by the existing FastAPI routes."""
    worker = harness.make_worker(
        request_timeout=request_timeout,
        queue_size=queue_size,
        real_com_apartment=real_com_apartment,
    )
    cache = dashboard_api.DashboardCache()
    monkeypatch.setattr(dashboard_api, "_cad_worker", worker)
    monkeypatch.setattr(dashboard_api, "_cache", cache)
    return worker, cache


def test_dashboard_routes_use_one_worker_thread_and_cache_routes_stay_plain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise every live dashboard CAD route through one real worker."""
    if sys.platform != "win32":
        pytest.skip("Dashboard COM apartment integration requires Windows")
    harness = _WorkerHarness()
    worker, _cache = _install_dashboard(
        monkeypatch,
        harness,
        real_com_apartment=True,
    )

    with TestClient(dashboard_api.api_app) as client:
        refresh = client.post("/api/cad/refresh")
        export = client.post("/api/cad/export")
        switch = client.post(
            "/api/cad/switch_drawing",
            json={"drawing_name": "B.dwg"},
        )
        entities = client.get(
            "/api/cad/entities",
            params={"page": 2, "limit": 2, "type": "Line"},
        )

        assert refresh.status_code == 200
        assert refresh.json() == {
            "success": True,
            "detail": "Refresh completed",
            "connected": True,
        }
        assert export.status_code == 200
        assert export.json() == {"success": True, "detail": "Export completed"}
        assert switch.status_code == 200
        assert switch.json() == {
            "success": True,
            "message": "Switched to B.dwg",
        }
        assert entities.status_code == 200
        assert entities.json()["entities"] == [{"Handle": "3", "Type": "Line", "Layer": "0"}]
        assert entities.json()["pagination"] == {
            "page": 2,
            "limit": 2,
            "total": 3,
            "total_pages": 2,
            "type": "Line",
            "dxf_type": "LINE",
            "cache_generation": 2,
            "cad_revision": entities.json()["pagination"]["cad_revision"],
            "cache_stale": False,
        }
        assert entities.json()["pagination"]["cad_revision"] > 0

        extract_calls = [
            details
            for name, _thread_id, details in harness.calls()
            if name == "adapter:extract_drawing_data"
        ]
        assert extract_calls == [
            {
                "only_selected": False,
                "limit": 2,
                "offset": 2,
                "entity_type": "LINE",
            }
        ]
        assert any(
            name == "adapter:switch_drawing" and details["drawing_name"] == "B.dwg"
            for name, _thread_id, details in harness.calls()
        )

        adapter_calls_before_cache_reads = harness.adapter_call_count()
        cached_responses = [
            client.get("/api/cad/status"),
            client.get("/api/cad/layers"),
            client.get("/api/cad/blocks"),
            client.get("/api/cad/drawings"),
            client.get("/api/debug/registry"),
            client.get("/api/logs"),
        ]
        assert all(response.status_code == 200 for response in cached_responses)
        assert harness.adapter_call_count() == adapter_calls_before_cache_reads

        worker_thread_id = worker.thread_id
        assert worker_thread_id is not None
        assert harness.owner_thread_id == worker_thread_id
        assert worker.status()["alive"] is True
        assert harness.real_com_initialized.is_set()

    assert worker.status()["alive"] is False
    assert harness.count("com:init") == 1
    assert harness.count("com:uninit") == 1
    assert harness.count("cleanup") == 1
    assert harness.count("adapter:disconnect") == 1
    assert {thread_id for _name, thread_id, _details in harness.calls()} == {worker_thread_id}
    call_names = harness.names()
    assert call_names[0] == "com:init"
    assert call_names.index("cleanup") < call_names.index("adapter:disconnect")
    assert call_names[-1] == "com:uninit"


def test_cached_dashboard_routes_do_not_start_or_touch_the_worker_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prove ordinary dashboard reads use only a copied cache snapshot."""
    harness = _WorkerHarness()
    worker, cache = _install_dashboard(monkeypatch, harness)
    cache.replace(
        {
            "connected": True,
            "cad_type": "autocad",
            "drawings": ["A.dwg"],
            "current_drawing": "A.dwg",
            "layers": [{"Name": "0"}],
            "blocks": [{"Name": "BlockA"}],
            "entities": [],
            "entity_counts": {"Line": 1},
            "total_entities": 1,
        }
    )

    with TestClient(dashboard_api.api_app) as client:
        for path in (
            "/api/health",
            "/api/cad/status",
            "/api/cad/layers",
            "/api/cad/blocks",
            "/api/cad/drawings",
            "/api/debug/registry",
            "/api/logs",
        ):
            assert client.get(path).status_code == 200
        assert worker.thread_id is None
        assert harness.calls() == []

    assert worker.status()["closing"] is True
    assert harness.calls() == []


@pytest.mark.asyncio
async def test_running_timeout_reports_unknown_outcome_without_blocking_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A running CAD timeout is explicit while unrelated ASGI work stays responsive."""
    harness = _WorkerHarness(block_export=True)
    worker, _cache = _install_dashboard(
        monkeypatch,
        harness,
        request_timeout=0.15,
    )
    transport = httpx.ASGITransport(app=dashboard_api.api_app)
    export_task: asyncio.Task[httpx.Response] | None = None

    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            export_task = asyncio.create_task(client.post("/api/cad/export"))
            assert await asyncio.to_thread(harness.export_entered.wait, 1.0)

            health = await asyncio.wait_for(client.get("/api/health"), timeout=0.5)
            assert health.status_code == 200
            assert health.json()["status"] == "ok"

            response = await asyncio.wait_for(export_task, timeout=1.0)
            assert response.status_code == 504
            assert response.json()["success"] is False
            assert response.json()["error_code"] == "cad_timeout"
            assert response.json()["outcome_unknown"] is True
    finally:
        harness.export_release.set()
        assert await asyncio.to_thread(harness.export_finished.wait, 1.0)
        if export_task is not None and not export_task.done():
            await asyncio.wait_for(export_task, timeout=1.0)
        assert await asyncio.to_thread(worker.shutdown, 1.0)


def test_cancelled_queued_ticket_never_reaches_the_adapter() -> None:
    """Cancel a queued request while a prior CAD operation owns the worker."""
    harness = _WorkerHarness(block_export=True)
    worker = harness.make_worker(request_timeout=2.0, queue_size=2)

    try:
        running = worker.submit("export")
        assert harness.export_entered.wait(timeout=1.0)
        queued = worker.submit("switch_drawing", {"drawing_name": "B.dwg"})
        assert worker.status()["queue_depth"] == 1

        assert queued.cancel() is True
        harness.export_release.set()
        running_result = running.future.result(timeout=1.0)
        assert running_result.pop("cad_revision") > 0
        assert running_result == {
            "success": True,
            "detail": "Export completed",
        }
        with pytest.raises(CancelledError):
            queued.future.result(timeout=1.0)
    finally:
        harness.export_release.set()
        assert worker.shutdown(timeout=1.0)

    assert "adapter:switch_drawing" not in harness.names()


def test_shutdown_fails_queued_work_and_uninitializes_once() -> None:
    """Shutdown rejects queued/new work while allowing an in-flight call to unwind."""
    harness = _WorkerHarness(block_export=True)
    worker = harness.make_worker(request_timeout=2.0, queue_size=2)

    running = worker.submit("export")
    assert harness.export_entered.wait(timeout=1.0)
    queued = worker.submit("switch_drawing", {"drawing_name": "B.dwg"})
    assert worker.status()["queue_depth"] == 1

    assert worker.shutdown(timeout=0.0) is False
    with pytest.raises(CadWorkerStoppedError):
        queued.future.result(timeout=0.5)
    with pytest.raises(CadWorkerStoppedError):
        worker.submit("refresh")

    harness.export_release.set()
    running_result = running.future.result(timeout=1.0)
    assert running_result.pop("cad_revision") > 0
    assert running_result == {
        "success": True,
        "detail": "Export completed",
    }
    assert worker.shutdown(timeout=1.0) is True

    assert "adapter:switch_drawing" not in harness.names()
    assert harness.count("com:init") == 1
    assert harness.count("cleanup") == 1
    assert harness.count("adapter:disconnect") == 1
    assert harness.count("com:uninit") == 1


def test_parent_http_lifespan_rebuilds_one_dashboard_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The composed parent lifespan closes and replaces exactly one dashboard worker."""
    import server as server_module

    first_harness = _WorkerHarness()
    first_worker = first_harness.make_worker()
    second_harness = _WorkerHarness()
    second_worker = second_harness.make_worker()
    replacements: list[DashboardCadWorker] = []

    def replacement_worker_factory() -> DashboardCadWorker:
        replacements.append(second_worker)
        return second_worker

    monkeypatch.setattr(dashboard_api, "_cad_worker", first_worker)
    monkeypatch.setattr(dashboard_api, "_cache", dashboard_api.DashboardCache())
    monkeypatch.setattr(
        dashboard_api,
        "DashboardCadWorker",
        replacement_worker_factory,
    )
    parent_app = server_module.build_http_app()

    with TestClient(parent_app) as client:
        response = client.post("/api/cad/refresh")
        assert response.status_code == 200
        assert first_worker.status()["alive"] is True
        assert dashboard_api._cad_worker is first_worker
        assert replacements == []

    assert first_worker.status()["closing"] is True
    assert first_worker.status()["alive"] is False
    assert first_harness.count("com:init") == 1
    assert first_harness.count("com:uninit") == 1

    with TestClient(parent_app) as client:
        response = client.post("/api/cad/refresh")
        assert response.status_code == 200
        assert dashboard_api._cad_worker is second_worker
        assert second_worker.status()["alive"] is True
        assert first_worker.status()["alive"] is False
        assert sum(worker.status()["alive"] for worker in (first_worker, second_worker)) == 1

    assert replacements == [second_worker]
    assert second_worker.status()["closing"] is True
    assert second_worker.status()["alive"] is False
    assert second_harness.count("com:init") == 1
    assert second_harness.count("com:uninit") == 1


def test_com_initialization_failure_returns_stopped_without_orphaned_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An immediate COM startup failure fails the HTTP request before its deadline."""
    harness = _WorkerHarness()

    def fail_com_initialization() -> None:
        harness.com_initialize()
        raise RuntimeError("simulated COM initialization failure")

    worker = DashboardCadWorker(
        adapter_provider=harness.adapter_provider,
        cad_type_provider=harness.cad_type_provider,
        request_timeout=2.0,
        queue_size=2,
        com_initialize=fail_com_initialization,
        com_uninitialize=harness.com_uninitialize,
        worker_cleanup=harness.cleanup,
    )
    monkeypatch.setattr(dashboard_api, "_cad_worker", worker)
    monkeypatch.setattr(dashboard_api, "_cache", dashboard_api.DashboardCache())

    started_at = time.monotonic()
    with TestClient(dashboard_api.api_app) as client:
        response = client.post("/api/cad/refresh")
        elapsed = time.monotonic() - started_at

        assert response.status_code == 503
        assert response.json()["error_code"] == "cad_worker_stopped"
        assert response.json()["outcome_unknown"] is False
        assert elapsed < 1.0
        assert worker.status()["closing"] is True
        assert worker.shutdown(timeout=0.5) is True
        assert worker.status()["alive"] is False
        assert worker.status()["queue_depth"] == 0
        assert worker.status()["startup_error"] == "simulated COM initialization failure"

    assert harness.count("com:init") == 1
    assert harness.count("provider") == 0
    assert harness.adapter_call_count() == 0
    assert harness.count("cleanup") == 0
    assert harness.count("com:uninit") == 0


def test_process_cad_gate_deadline_expires_before_adapter_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A gate held by an MCP-side operation times out without entering fake CAD code."""
    harness = _WorkerHarness()
    worker, _cache = _install_dashboard(
        monkeypatch,
        harness,
        request_timeout=0.15,
    )

    with TestClient(dashboard_api.api_app) as client:
        with cad_operation():
            response = client.post("/api/cad/refresh")

        assert response.status_code == 504
        assert response.json()["error_code"] == "cad_timeout"
        assert response.json()["outcome_unknown"] is False
        assert harness.count("provider") == 0
        assert harness.adapter_call_count() == 0

    assert worker.status()["alive"] is False
    assert worker.status()["queue_depth"] == 0
    assert harness.count("com:init") == 1
    assert harness.count("com:uninit") == 1


def test_shutdown_completes_future_callbacks_outside_the_state_lock() -> None:
    """A queued Future callback may inspect worker status without deadlocking shutdown."""
    harness = _WorkerHarness(block_export=True)
    worker = harness.make_worker(request_timeout=2.0, queue_size=2)
    callback_finished = threading.Event()

    running = worker.submit("export")
    assert harness.export_entered.wait(timeout=1.0)
    queued = worker.submit("switch_drawing", {"drawing_name": "B.dwg"})

    def inspect_status(_future: Any) -> None:
        worker.status()
        callback_finished.set()

    queued.future.add_done_callback(inspect_status)
    try:
        assert worker.shutdown(timeout=0.0) is False
        assert callback_finished.wait(timeout=0.5)
    finally:
        harness.export_release.set()
        running.future.result(timeout=1.0)
        assert worker.shutdown(timeout=1.0)


def test_dashboard_cache_rejects_an_older_cad_revision() -> None:
    """A delayed publisher cannot replace a newer drawing snapshot."""
    cache = dashboard_api.DashboardCache()
    newer = {
        "connected": True,
        "cad_type": "autocad",
        "current_drawing": "B.dwg",
        "drawings": ["A.dwg", "B.dwg"],
        "cad_revision": 20,
    }
    older = {
        "connected": True,
        "cad_type": "autocad",
        "current_drawing": "A.dwg",
        "drawings": ["A.dwg", "B.dwg"],
        "cad_revision": 19,
    }

    assert cache.replace(newer) is True
    assert cache.replace(older) is False
    snapshot = cache.snapshot()
    assert snapshot["current_drawing"] == "B.dwg"
    assert snapshot["cad_revision"] == 20
    assert snapshot["generation"] == 1
