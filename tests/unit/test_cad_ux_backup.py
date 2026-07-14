"""Tests for safe DWG backup path checks and bounded retention."""

from datetime import datetime, timedelta

import pytest

from cad_ux.backup import BackupSafetyError, create_backup, drawing_source


class FakeDocument:
    def __init__(self, full_name: str = "", path: str = "", saved: bool = False):
        """Create an AutoCAD-document-shaped test double."""
        self.FullName = full_name
        self.Path = path
        self.Saved = saved


def test_unsaved_or_dirty_drawing_is_blocked(tmp_path):
    with pytest.raises(BackupSafetyError, match="never been saved"):
        drawing_source(FakeDocument())
    source = tmp_path / "part.dwg"
    source.write_bytes(b"dwg")
    with pytest.raises(BackupSafetyError, match="unsaved changes"):
        drawing_source(FakeDocument(str(source), str(tmp_path), False))


def test_saved_drawing_path_is_accepted(tmp_path):
    source = tmp_path / "part.dwg"
    source.write_bytes(b"dwg")
    assert drawing_source(FakeDocument(str(source), str(tmp_path), True)) == source


def test_backup_retention_preserves_original_and_unrelated_files(tmp_path):
    source = tmp_path / "part.dwg"
    source.write_bytes(b"original")
    unrelated_dir = tmp_path / "AI_Backups"
    unrelated_dir.mkdir()
    unrelated = unrelated_dir / "another_part.AI_BACKUP.20000101-000000-000000.dwg"
    unrelated.write_bytes(b"keep")
    start = datetime(2026, 7, 14, 1, 0, 0)
    for index in range(22):
        create_backup(source, keep=20, now=start + timedelta(seconds=index))
    own_backups = list(unrelated_dir.glob("part.AI_BACKUP.*.dwg"))
    assert len(own_backups) == 20
    assert source.read_bytes() == b"original"
    assert unrelated.read_bytes() == b"keep"
