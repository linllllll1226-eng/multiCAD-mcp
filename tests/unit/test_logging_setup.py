"""Regression coverage for read-only deployment logging."""

import logging

import pytest

from mcp_tools import helpers


def test_explicit_log_directory_writes_utf8(monkeypatch, tmp_path):
    monkeypatch.setenv("MULTICAD_LOG_DIR", str(tmp_path / "logs"))
    captured = {}
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: captured.update(kw))
    helpers.setup_logging()
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "日志 smoke", (), None)
    for handler in captured["handlers"]:
        if isinstance(handler, logging.FileHandler):
            handler.emit(record)
        handler.close()
    assert "日志 smoke" in (tmp_path / "logs" / "multicad_mcp.log").read_text("utf-8")


@pytest.mark.parametrize("failure_stage", ["directory", "file"])
def test_permission_denied_keeps_stderr(monkeypatch, tmp_path, caplog, failure_stage):
    monkeypatch.setenv("MULTICAD_LOG_DIR", str(tmp_path))
    captured = {}
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: captured.update(kw))

    def denied(*args, **kwargs):
        raise PermissionError("deployment is read-only")

    if failure_stage == "directory":
        monkeypatch.setattr(helpers.os, "makedirs", denied)
    else:
        monkeypatch.setattr(logging, "FileHandler", denied)
    helpers.setup_logging()
    assert len(captured["handlers"]) == 1
    assert type(captured["handlers"][0]) is logging.StreamHandler
    assert "Continuing with stderr" in caplog.text
    assert "MULTICAD_LOG_DIR" in caplog.text
