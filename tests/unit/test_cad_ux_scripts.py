"""Static safety checks for optional PowerShell and profile sync scripts."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_start_script_checks_stable_entry_and_never_opens_or_saves_dwg():
    text = (ROOT / "scripts" / "Start-CAD-AI.ps1").read_text(encoding="utf-8")
    assert "server_memory.py" in text
    assert "cad_memory.db" in text
    assert "GetActiveObject('AutoCAD.Application')" in text
    assert "Documents.Open" not in text
    assert "SaveAs" not in text
    assert ".Save()" not in text


def test_template_initializer_is_dry_run_by_default_and_does_not_save():
    text = (ROOT / "scripts" / "Initialize-AI-DrawingTemplate.ps1").read_text(
        encoding="utf-8"
    )
    assert "ApplyToBlankDrawing" in text
    assert "ModelSpace.Count" in text
    assert "AI_UNCERTAIN" in text
    assert "CENTER2" in text
    assert "HIDDEN2" in text
    assert "SaveAs(" not in text


def test_profile_sync_reuses_existing_mcp_tools():
    text = (ROOT / "scripts" / "Sync-CAD-Profiles.py").read_text(encoding="utf-8")
    assert '"cad_save_drawing_profile"' in text
    assert '"cad_load_drawing_profile"' in text
    assert "--apply" in text
