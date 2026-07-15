from scripts.check_repository_hygiene import forbidden_tracked_paths


def test_forbidden_tracked_paths_detects_runtime_artifacts() -> None:
    paths = [
        "src/server.py",
        "data/cad_memory.db",
        "logs/multicad_mcp.log",
        "drawing/test.dwg",
        "scripts/Start-CAD-AI.ps1.bak-20260714",
        "AUTOCAD_CODEX_SETUP_SUMMARY.md",
    ]
    assert forbidden_tracked_paths(paths) == paths[1:]


def test_forbidden_tracked_paths_allows_release_sources() -> None:
    assert (
        forbidden_tracked_paths(
            [
                "README.md",
                "src/cad_memory/database.py",
                "docs/CAD_TASK_TRACKING.md",
                "logs/multicad_mcp.example.log",
            ]
        )
        == []
    )
