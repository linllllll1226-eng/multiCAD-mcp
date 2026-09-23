"""Locate immutable wheel resources and writable per-user runtime data."""

import os
from pathlib import Path


def resource_path(name: str) -> Path:
    """Return a bundled resource without depending on the working directory."""
    root = Path(__file__).resolve().parent
    target = (root / name).resolve()
    if root not in target.parents:
        raise ValueError("Resource must remain inside the runtime package")
    return target


def data_directory() -> Path:
    """Keep checkout data local and installed-package data in the user profile."""
    override = os.environ.get("MULTICAD_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    project = Path(__file__).resolve().parents[2]
    if (project / "pyproject.toml").is_file() and (project / "src").is_dir():
        return project / "data"
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share")))
    return base / "multiCAD-mcp" / "data"
