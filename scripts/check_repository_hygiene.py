"""Fail CI when release sources contain local runtime or uncommitted files."""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath

FORBIDDEN_TRACKED_PARTS = {
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "logs",
    "site",
}
FORBIDDEN_TRACKED_SUFFIXES = {
    ".db",
    ".db-shm",
    ".db-wal",
    ".dwg",
    ".dwl",
    ".dwl2",
    ".pyc",
    ".sqlite",
    ".sqlite3",
}


def _git(root: Path, *args: str) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return [line for line in completed.stdout.splitlines() if line]


def forbidden_tracked_paths(paths: list[str]) -> list[str]:
    """Return tracked paths that are runtime, CAD, database, or cache artifacts."""
    forbidden: list[str] = []
    for raw_path in paths:
        path = PurePosixPath(raw_path.replace("\\", "/"))
        lower_name = path.name.casefold()
        is_documented_example = lower_name.endswith(".example.log")
        has_forbidden_part = not is_documented_example and any(
            part.casefold() in FORBIDDEN_TRACKED_PARTS for part in path.parts
        )
        has_forbidden_suffix = any(
            lower_name.endswith(suffix) for suffix in FORBIDDEN_TRACKED_SUFFIXES
        )
        is_local_note = lower_name == "autocad_codex_setup_summary.md"
        is_script_backup = ".bak-" in lower_name
        if (
            has_forbidden_part
            or (has_forbidden_suffix and not is_documented_example)
            or is_local_note
            or is_script_backup
        ):
            forbidden.append(raw_path)
    return forbidden


def main() -> int:
    """Check the repository and print a compact release-hygiene report."""
    root = Path(__file__).resolve().parents[1]
    dirty = _git(root, "status", "--porcelain", "--untracked-files=all")
    tracked = _git(root, "ls-files")
    forbidden = forbidden_tracked_paths(tracked)
    if dirty or forbidden:
        if dirty:
            print("Uncommitted or untracked release files:")
            print("\n".join(dirty))
        if forbidden:
            print("Forbidden tracked runtime artifacts:")
            print("\n".join(forbidden))
        return 1
    print(f"Repository hygiene passed: {len(tracked)} tracked files, clean worktree")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
