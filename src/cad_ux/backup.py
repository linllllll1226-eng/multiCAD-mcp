"""Create bounded, timestamped backups before a formal CAD write."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


class BackupSafetyError(RuntimeError):
    """Raised when the active drawing is not safe to back up automatically."""


def drawing_source(document: Any) -> Path:
    """Return a saved, unchanged DWG path or block the formal write."""
    drawing_path = str(getattr(document, "Path", "") or "").strip()
    full_name = str(getattr(document, "FullName", "") or "").strip()
    if not drawing_path or not full_name:
        raise BackupSafetyError(
            "The active drawing has never been saved. "
            "Save it manually before a formal write."
        )
    if not bool(getattr(document, "Saved", False)):
        raise BackupSafetyError(
            "The active drawing has unsaved changes. "
            "Save it manually before a formal write."
        )
    source = Path(full_name).resolve()
    if source.suffix.lower() != ".dwg" or not source.is_file():
        raise BackupSafetyError(f"Saved DWG file is not accessible: {source}")
    return source


def create_backup(
    source: str | Path,
    *,
    keep: int = 20,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Copy one DWG and prune only older AI backup copies for that DWG."""
    source_path = Path(source).resolve()
    if keep < 1:
        raise ValueError("keep must be at least 1")
    if source_path.suffix.lower() != ".dwg" or not source_path.is_file():
        raise BackupSafetyError(f"Source DWG file is not accessible: {source_path}")

    backup_dir = source_path.parent / "AI_Backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S-%f")
    destination = backup_dir / (
        f"{source_path.stem}.AI_BACKUP.{timestamp}{source_path.suffix}"
    )
    shutil.copy2(source_path, destination)

    pattern = f"{source_path.stem}.AI_BACKUP.*{source_path.suffix}"
    candidates = sorted(
        (path for path in backup_dir.glob(pattern) if path.is_file()),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    removed: list[str] = []
    for stale in candidates[keep:]:
        stale.unlink()
        removed.append(str(stale))

    return {
        "source": str(source_path),
        "backup": str(destination),
        "backup_directory": str(backup_dir),
        "retention_limit": keep,
        "removed_old_ai_backups": removed,
        "original_deleted": False,
    }


def backup_active_document(*, keep: int = 20) -> dict[str, Any]:
    """Read the active AutoCAD document and back up its on-disk DWG only."""
    import win32com.client

    application = win32com.client.GetActiveObject("AutoCAD.Application")
    document = application.ActiveDocument
    source = drawing_source(document)
    result = create_backup(source, keep=keep)
    result.update(
        {
            "autocad_version": str(application.Version),
            "drawing_name": str(document.Name),
            "drawing_saved": bool(document.Saved),
        }
    )
    return result


def main() -> None:
    """Run the guarded backup helper from the project virtual environment."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", type=int, default=20)
    args = parser.parse_args()
    try:
        result = backup_active_document(keep=args.keep)
    except BackupSafetyError as exc:
        print(json.dumps({"passed": False, "error": str(exc)}), file=sys.stderr)
        raise SystemExit(2) from exc
    print(json.dumps({"passed": True, **result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
