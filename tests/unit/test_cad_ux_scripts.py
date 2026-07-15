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


def test_template_initializer_is_dry_run_by_default_and_guards_template_save():
    text = (ROOT / "scripts" / "Initialize-AI-DrawingTemplate.ps1").read_text(encoding="utf-8")
    assert "ApplyToBlankDrawing" in text
    assert "ModelSpace.Count" in text
    assert "AI_UNCERTAIN" in text
    assert "CENTER2" in text
    assert "HIDDEN2" in text
    assert "SaveTemplate" in text
    assert "SaveAs($TemplateTarget, 66)" in text
    assert "Refusing to overwrite existing template" in text
    assert "SaveTemplate -and -not $ApplyToBlankDrawing" in text


def test_profile_sync_reuses_existing_mcp_tools():
    text = (ROOT / "scripts" / "Sync-CAD-Profiles.py").read_text(encoding="utf-8")
    assert '"cad_save_drawing_profile"' in text
    assert '"cad_load_drawing_profile"' in text
    assert "--apply" in text


def test_task_acceptance_is_isolated_guarded_and_non_overwriting():
    text = (ROOT / "scripts" / "Run-CAD-Task-Tracking-Acceptance.py").read_text(encoding="utf-8")
    assert 'DispatchEx("AutoCAD.Application.24.1")' in text
    assert "PlanValidator().validate" in text
    assert "PlanExecutor().execute" in text
    assert "PostExecutionVerifier().verify" in text
    assert "commit_preview_task" in text
    assert "revert_task" in text
    assert "Refusing to overwrite" in text
    assert "ac2018_Template" in text
