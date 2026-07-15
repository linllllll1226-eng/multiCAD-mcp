"""Create the DWT and accept task tracking in an isolated AutoCAD instance."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pythoncom  # noqa: E402
import pywintypes  # noqa: E402
import win32com.client  # noqa: E402

from adapters.autocad_adapter import AutoCADAdapter  # noqa: E402
from cad_memory.database import SQLiteMemoryStore  # noqa: E402
from cad_memory.executor import PlanExecutor  # noqa: E402
from cad_memory.models import DrawingPlan  # noqa: E402
from cad_memory.provenance import (  # noqa: E402
    document_identity,
    generate_task_id,
    read_entity_provenance,
)
from cad_memory.task_manager import REVERT_LAYER, TaskTrackingManager  # noqa: E402
from cad_memory.validator import PlanValidator  # noqa: E402
from cad_memory.verifier import PostExecutionVerifier  # noqa: E402

LAYER_DEFINITIONS = {
    "OUTLINE": (7, "Continuous"),
    "CENTER": (1, "CENTER2"),
    "HIDDEN": (2, "HIDDEN2"),
    "HATCH": (3, "Continuous"),
    "DIM": (6, "Continuous"),
    "TEXT": (7, "Continuous"),
    "AI_PREVIEW_OUTLINE": (7, "Continuous"),
    "AI_PREVIEW_CENTER": (1, "CENTER2"),
    "AI_PREVIEW_HIDDEN": (2, "HIDDEN2"),
    "AI_PREVIEW_HATCH": (3, "Continuous"),
    "AI_PREVIEW_DIM": (6, "Continuous"),
    "AI_UNCERTAIN": (4, "HIDDEN2"),
}


def _retry_com(action, *, timeout: float = 30.0):
    """Retry calls AutoCAD rejects temporarily while starting or regenerating."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            return action()
        except pywintypes.com_error as exc:
            if exc.hresult != -2147418111 or time.monotonic() >= deadline:
                raise
            time.sleep(0.5)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--template-target",
        type=Path,
        default=Path(r"D:\AI\CAD_Templates\AI_Drawing_Template.dwt"),
    )
    parser.add_argument("--reuse-existing-template", action="store_true")
    parser.add_argument(
        "--acceptance-dwg",
        type=Path,
        default=Path.cwd() / "cad_task_tracking_acceptance.dwg",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path.cwd() / "cad_task_tracking_acceptance.db",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path.cwd() / "cad_task_tracking_acceptance.json",
    )
    return parser.parse_args()


def _refuse_existing(paths: list[Path]) -> None:
    existing = [str(path.resolve()) for path in paths if path.exists()]
    if existing:
        raise FileExistsError(f"Refusing to overwrite: {existing}")


def _ensure_linetype(document: Any, name: str) -> None:
    try:
        document.Linetypes.Item(name)
    except Exception:
        try:
            document.Linetypes.Load(name, "acadiso.lin")
        except Exception:
            document.Linetypes.Load(name, "acad.lin")


def _prepare_layers(document: Any) -> None:
    for linetype in ("CENTER2", "HIDDEN2"):
        _ensure_linetype(document, linetype)
    for name, (color, linetype) in LAYER_DEFINITIONS.items():
        try:
            layer = document.Layers.Item(name)
        except Exception:
            layer = document.Layers.Add(name)
        layer.Color = color
        layer.Linetype = linetype


def _wait_document_ready(document: Any) -> None:
    _retry_com(lambda: int(document.ModelSpace.Count))
    _retry_com(lambda: int(document.Layers.Count))
    time.sleep(1.0)


def _configure_template(document: Any) -> None:
    if int(document.ModelSpace.Count) != 0:
        raise RuntimeError("Template document is not empty")
    _prepare_layers(document)
    try:
        style = document.TextStyles.Item("AI_STANDARD")
    except Exception:
        style = document.TextStyles.Add("AI_STANDARD")
    try:
        style.SetFont("Microsoft YaHei", False, False, 0, 134)
    except Exception:
        try:
            style.SetFont("Arial", False, False, 0, 34)
        except Exception:
            style.FontFile = "txt.shx"
    variables = {
        "INSUNITS": 4,
        "MEASUREMENT": 1,
        "LUNITS": 2,
        "LUPREC": 2,
        "TEXTSTYLE": "AI_STANDARD",
        "TEXTSIZE": 3.5,
        "DIMTXT": 3.5,
        "DIMASZ": 2.5,
        "DIMGAP": 1.0,
        "DIMDEC": 2,
        "DIMTAD": 1,
        "DIMTXSTY": "AI_STANDARD",
        "LTSCALE": 1.0,
        "MSLTSCALE": 1,
        "PSLTSCALE": 1,
    }
    for name, value in variables.items():
        document.SetVariable(name, value)
    try:
        dim_style = document.DimStyles.Item("AI_STANDARD_DIM")
    except Exception:
        dim_style = document.DimStyles.Add("AI_STANDARD_DIM")
    dim_style.CopyFrom(document)
    document.ActiveDimStyle = dim_style
    document.Regen(1)


class AcceptanceAdapter(AutoCADAdapter):
    """Pin the adapter to the isolated acceptance document without reconnecting."""

    def _validate_connection(self) -> None:
        if self.application is None or self.document is None:
            raise RuntimeError("Acceptance AutoCAD connection is unavailable")

    def _get_application(self, _operation: str = "operation") -> Any:
        self._validate_connection()
        return self.application

    def _get_document(self, _operation: str = "operation") -> Any:
        self._validate_connection()
        return self.document

    def list_layers(self) -> list[str]:
        """Read layers after AutoCAD finishes creating the document."""
        return _retry_com(lambda: [str(layer.Name) for layer in self.document.Layers])


def _adapter(application: Any, document: Any) -> AutoCADAdapter:
    adapter = AcceptanceAdapter("autocad")
    adapter.application = application
    adapter.document = document
    return adapter


def _circle_plan(task_name: str, center: list[float], radius: float) -> DrawingPlan:
    return DrawingPlan.model_validate(
        {
            "task_name": task_name,
            "drawing_profile": "general_2d",
            "unit": "mm",
            "user_confirmed": True,
            "preview_mode": True,
            "entities": [
                {
                    "entity_type": "circle",
                    "coordinates": {"center": center},
                    "dimensions": {"radius": radius},
                    "layer": "AI_PREVIEW_OUTLINE",
                    "linetype": "ByLayer",
                    "dimension_source": "explicit_dimension",
                    "confidence": 1,
                }
            ],
        }
    )


def _run_guarded_task(
    adapter: AutoCADAdapter,
    store: SQLiteMemoryStore,
    plan: DrawingPlan,
) -> dict[str, Any]:
    validation = PlanValidator().validate(plan, available_layers=adapter.list_layers())
    if not validation.passed:
        raise RuntimeError(f"cad_plan_validate failed: {validation.to_dict()}")
    task_id = generate_task_id()
    identity = document_identity(adapter.document)
    pending = store.record_execution(
        task_name=plan.task_name,
        planned_data=plan.model_dump(mode="json"),
        actual_data={"task_id": task_id, "status": "pending"},
        passed=False,
        errors=["pending"],
    )
    store.create_ai_task(
        task_id=task_id,
        task_name=plan.task_name,
        drawing_name=identity["drawing_name"],
        drawing_full_name=identity["drawing_full_name"],
        drawing_profile=plan.drawing_profile or "",
        status="executing",
        execution_result_id=pending["id"],
        plan_data=plan.model_dump(mode="json"),
    )
    execution = PlanExecutor().execute(
        adapter,
        plan,
        task_id=task_id,
        execution_result_id=pending["id"],
    )
    if not execution["success"]:
        store.update_ai_task(task_id, status="failed")
        raise RuntimeError(f"cad_execute_plan failed: {execution}")
    store.add_ai_task_entities(task_id, execution["entity_records"])
    store.update_execution_result(
        pending["id"],
        actual_data=execution,
        passed=True,
        errors=[],
    )
    store.update_ai_task(task_id, status="executed")
    verification = PostExecutionVerifier().verify(adapter, plan, execution["handles"])
    provenance_errors = []
    for handle in execution["handles"]:
        metadata = read_entity_provenance(adapter.document.HandleToObject(handle))
        if not metadata or metadata.get("task_id") != task_id:
            provenance_errors.append(f"{handle}: provenance mismatch")
    if provenance_errors:
        verification["passed"] = False
        verification["errors"].extend(provenance_errors)
    verification_record = store.record_execution(
        task_name=f"{plan.task_name}:verification",
        planned_data=plan.model_dump(mode="json"),
        actual_data=verification,
        passed=verification["passed"],
        errors=verification["errors"],
    )
    store.update_ai_task(
        task_id,
        status="verified" if verification["passed"] else "failed",
        verification_data=verification,
    )
    if not verification["passed"]:
        raise RuntimeError(f"cad_verify_execution failed: {verification}")
    return {
        "task_id": task_id,
        "handles": execution["handles"],
        "validation": validation.to_dict(),
        "execution_result_id": pending["id"],
        "verification_result_id": verification_record["id"],
        "verification": verification,
    }


def _save_template(application: Any, target: Path) -> dict[str, Any]:
    document = application.ActiveDocument
    if int(document.ModelSpace.Count) != 0:
        document = application.Documents.Add()
    _configure_template(document)
    _wait_document_ready(document)
    target.parent.mkdir(parents=True, exist_ok=True)
    _retry_com(lambda: document.SaveAs(str(target.resolve()), 66))  # ac2018_Template
    if not target.exists():
        raise RuntimeError(f"Template was not created: {target}")
    result = {
        "path": str(target.resolve()),
        "size": target.stat().st_size,
        "layers": sorted(str(layer.Name) for layer in document.Layers),
        "insunits": int(document.GetVariable("INSUNITS")),
        "text_style": str(document.GetVariable("TEXTSTYLE")),
        "dim_style": str(document.ActiveDimStyle.Name),
    }
    _retry_com(lambda: document.Close(True))
    return result


def _verify_template_roundtrip(application: Any, target: Path) -> dict[str, Any]:
    """Create a new drawing from the saved DWT and verify persisted settings."""
    document = _retry_com(lambda: application.Documents.Add(str(target.resolve())))
    _wait_document_ready(document)
    actual_layers = {str(layer.Name) for layer in document.Layers}
    expected_layers = set(LAYER_DEFINITIONS) | {"0"}
    missing_layers = sorted(expected_layers - actual_layers)
    result = {
        "created_from_template": True,
        "modelspace_count": int(document.ModelSpace.Count),
        "missing_layers": missing_layers,
        "insunits": int(document.GetVariable("INSUNITS")),
        "text_style": str(document.GetVariable("TEXTSTYLE")),
        "dim_style": str(document.ActiveDimStyle.Name),
    }
    expected = {
        "created_from_template": True,
        "modelspace_count": 0,
        "missing_layers": [],
        "insunits": 4,
        "text_style": "AI_STANDARD",
        "dim_style": "AI_STANDARD_DIM",
    }
    if result != expected:
        raise RuntimeError(f"DWT round-trip mismatch: {result} != {expected}")
    _retry_com(lambda: document.Close(False))
    return result


def _accept_tasks(
    application: Any,
    store: SQLiteMemoryStore,
    drawing_path: Path,
    template_path: Path,
) -> dict[str, Any]:
    document = _retry_com(lambda: application.Documents.Add(str(template_path.resolve())))
    adapter = _adapter(application, document)
    _wait_document_ready(document)
    task_a = _run_guarded_task(adapter, store, _circle_plan("task-a", [0, 0, 0], 10))
    task_b = _run_guarded_task(adapter, store, _circle_plan("task-b", [40, 0, 0], 5))
    manager = TaskTrackingManager(store)
    foreign_document = _retry_com(lambda: application.Documents.Add(str(template_path.resolve())))
    foreign_adapter = _adapter(application, foreign_document)
    _wait_document_ready(foreign_document)
    cross_drawing_error = ""
    try:
        manager.commit_preview_task(
            foreign_adapter,
            task_b["task_id"],
            confirmed=True,
        )
    except PermissionError as exc:
        cross_drawing_error = str(exc)
    if "different drawing" not in cross_drawing_error:
        raise RuntimeError("Cross-drawing task commit was not blocked by drawing identity")
    cross_drawing_guard = {
        "blocked": True,
        "error": cross_drawing_error,
        "foreign_modelspace_count": int(foreign_document.ModelSpace.Count),
    }
    _retry_com(lambda: foreign_document.Close(False))
    commit_manifest = manager.commit_preview_task(adapter, task_a["task_id"], confirmed=False)
    if not commit_manifest.get("requires_confirmation"):
        raise RuntimeError("Commit manifest was not required")
    commit = manager.commit_preview_task(adapter, task_a["task_id"], confirmed=True)
    revert_manifest = manager.revert_task(adapter, task_b["task_id"], confirmed=False)
    if not revert_manifest.get("requires_confirmation"):
        raise RuntimeError("Revert manifest was not required")
    revert = manager.revert_task(adapter, task_b["task_id"], confirmed=True)
    handle_a = task_a["handles"][0]
    handle_b = task_b["handles"][0]
    drawing_path.parent.mkdir(parents=True, exist_ok=True)
    _retry_com(lambda: document.SaveAs(str(drawing_path.resolve())))
    _retry_com(lambda: document.Close(True))
    reopened = _retry_com(lambda: application.Documents.Open(str(drawing_path.resolve())))
    persisted_a = reopened.HandleToObject(handle_a)
    persisted_b = reopened.HandleToObject(handle_b)
    metadata_a = read_entity_provenance(persisted_a)
    metadata_b = read_entity_provenance(persisted_b)
    persistence = {
        "task_a_layer": str(persisted_a.Layer),
        "task_b_layer": str(persisted_b.Layer),
        "task_a_id": (metadata_a or {}).get("task_id"),
        "task_b_id": (metadata_b or {}).get("task_id"),
        "task_a_status": (metadata_a or {}).get("status"),
        "task_b_status": (metadata_b or {}).get("status"),
        "revert_layer_visible": bool(reopened.Layers.Item(REVERT_LAYER).LayerOn),
    }
    expected = {
        "task_a_layer": "OUTLINE",
        "task_b_layer": REVERT_LAYER,
        "task_a_id": task_a["task_id"],
        "task_b_id": task_b["task_id"],
        "task_a_status": "committed",
        "task_b_status": "reverted",
        "revert_layer_visible": False,
    }
    if persistence != expected:
        raise RuntimeError(f"Save/reopen persistence mismatch: {persistence} != {expected}")
    _retry_com(lambda: reopened.Close(False))
    return {
        "drawing": str(drawing_path.resolve()),
        "task_a": task_a,
        "task_b": task_b,
        "cross_drawing_guard": cross_drawing_guard,
        "commit_manifest": commit_manifest,
        "commit": commit,
        "revert_manifest": revert_manifest,
        "revert": revert,
        "persistence": persistence,
    }


def main() -> int:
    """Run isolated DWT creation and task-tracking acceptance."""
    args = _arguments()
    targets = [args.acceptance_dwg, args.database, args.report]
    if args.reuse_existing_template:
        if not args.template_target.exists():
            raise FileNotFoundError(f"Template does not exist for reuse: {args.template_target}")
    else:
        targets.append(args.template_target)
    _refuse_existing(targets)
    pythoncom.CoInitialize()
    application = None
    report: dict[str, Any] = {
        "success": False,
        "isolated_autocad_instance": True,
        "guarded_sequence": [
            "cad_plan_validate",
            "cad_execute_plan",
            "cad_verify_execution",
        ],
    }
    try:
        application = win32com.client.DispatchEx("AutoCAD.Application.24.1")
        try:
            _retry_com(lambda: setattr(application, "Visible", True))
            report["autocad_visible"] = True
        except AttributeError:
            report["autocad_visible"] = False
        report["autocad_version"] = _retry_com(lambda: str(application.Version))
        if _retry_com(lambda: int(application.Documents.Count)) == 0:
            _retry_com(lambda: application.Documents.Add())
        if args.reuse_existing_template:
            report["template"] = {
                "path": str(args.template_target.resolve()),
                "size": args.template_target.stat().st_size,
                "reused": True,
            }
        else:
            report["template"] = _save_template(application, args.template_target)
        report["template"]["roundtrip"] = _verify_template_roundtrip(
            application, args.template_target
        )
        store = SQLiteMemoryStore(args.database)
        report["task_tracking"] = _accept_tasks(
            application, store, args.acceptance_dwg, args.template_target
        )
        report["success"] = True
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    finally:
        if application is not None:
            try:
                application.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
