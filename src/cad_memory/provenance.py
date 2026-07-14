"""Persistent AutoCAD XData provenance for AI-created entities."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

XDATA_APP_NAME = "CODEX_CAD_AI"
CREATED_BY = "codex_autocad_assistant"
_XDATA_CHUNK_SIZE = 240


def utc_now() -> str:
    """Return a stable UTC timestamp for task metadata."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def generate_task_id() -> str:
    """Generate a short sortable task identifier."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"cad_{stamp}_{uuid4().hex[:8]}"


def document_identity(document: Any) -> dict[str, str]:
    """Read the active document identity without changing it."""
    name = str(getattr(document, "Name", "") or "")
    full_name = str(getattr(document, "FullName", "") or "")
    path = str(getattr(document, "Path", "") or "")
    if not full_name and path and name:
        base_path = path.rstrip("\\/")
        full_name = f"{base_path}\\{name}"
    return {"drawing_name": name, "drawing_full_name": full_name}


def _ensure_registered_application(document: Any) -> None:
    applications = getattr(document, "RegisteredApplications", None)
    if applications is None:
        return
    try:
        applications.Item(XDATA_APP_NAME)
    except Exception:
        applications.Add(XDATA_APP_NAME)


def _xdata_arrays(adapter: Any, payload: str) -> tuple[Any, Any]:
    chunks = [
        payload[index : index + _XDATA_CHUNK_SIZE]
        for index in range(0, len(payload), _XDATA_CHUNK_SIZE)
    ] or [""]
    codes = [1001, *([1000] * len(chunks))]
    values = [XDATA_APP_NAME, *chunks]
    if hasattr(adapter, "_int_array_to_variant"):
        codes = adapter._int_array_to_variant(codes)
    if hasattr(adapter, "_mixed_array_to_variant"):
        values = adapter._mixed_array_to_variant(values)
    return codes, values


def write_entity_provenance(
    adapter: Any, document: Any, entity: Any, metadata: dict[str, Any]
) -> dict[str, Any]:
    """Write compact task metadata to one entity using registered XData."""
    normalized = dict(metadata)
    normalized["created_by"] = CREATED_BY
    payload = json.dumps(normalized, ensure_ascii=True, separators=(",", ":"))
    _ensure_registered_application(document)
    codes, values = _xdata_arrays(adapter, payload)
    if hasattr(entity, "SetXData"):
        entity.SetXData(codes, values)
    else:
        entity._codex_xdata = (list(codes), list(values))
    return normalized


def read_entity_provenance(entity: Any) -> dict[str, Any] | None:
    """Read and validate the assistant XData from one entity."""
    try:
        if hasattr(entity, "GetXData"):
            result = entity.GetXData(XDATA_APP_NAME)
        else:
            result = entity._codex_xdata
        if not result or len(result) != 2:
            return None
        values = list(result[1])
        if values and str(values[0]) == XDATA_APP_NAME:
            values = values[1:]
        payload = "".join(str(value) for value in values if value is not None)
        data = json.loads(payload)
        if not isinstance(data, dict) or data.get("created_by") != CREATED_BY:
            return None
        return data
    except Exception:
        return None


def build_entity_provenance(
    *,
    task_id: str,
    execution_result_id: int,
    drawing_profile: str,
    source_type: str,
    confidence: float,
    approximate_reference: bool,
    drawing_name: str = "",
    drawing_full_name: str = "",
    status: str = "preview",
) -> dict[str, Any]:
    """Build the required metadata for a newly created AI entity."""
    return {
        "task_id": task_id,
        "created_by": CREATED_BY,
        "created_at": utc_now(),
        "drawing_profile": drawing_profile,
        "source_type": source_type,
        "confidence": float(confidence),
        "approximate_reference": bool(approximate_reference),
        "execution_result_id": int(execution_result_id),
        "drawing_name": str(drawing_name or ""),
        "drawing_full_name": str(drawing_full_name or ""),
        "status": status,
    }
