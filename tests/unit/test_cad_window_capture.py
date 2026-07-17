"""Tests for robust CAD HWND selection and screenshot quality checks."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from PIL import Image

from cad_vision.window_capture import (
    WindowCandidate,
    capture_cad_window,
    image_quality,
    rank_window_candidates,
)


def test_direct_com_hwnd_wins_even_with_unexpected_afx_class():
    candidates = [
        WindowCandidate(10, "Autodesk AutoCAD 2022 - Drawing1.dwg", "Afx:00400000:b:00010003"),
        WindowCandidate(20, "AutoCAD VBA", "AutoCAD Main Window"),
    ]
    ranked = rank_window_candidates(
        candidates,
        cad_type="autocad",
        document_name="Drawing1.dwg",
        direct_hwnd=10,
    )
    assert ranked[0][1].hwnd == 10
    assert ranked[0][0] >= 1000


def test_document_and_process_match_beats_unrelated_autocad_dialog():
    candidates = [
        WindowCandidate(10, "Autodesk AutoCAD - About", "#32770", "acad.exe"),
        WindowCandidate(20, "Drawing1.dwg - Autodesk AutoCAD 2022", "Afx:main", "acad.exe"),
    ]
    ranked = rank_window_candidates(
        candidates,
        cad_type="autocad",
        document_name="Drawing1.dwg",
    )
    assert ranked[0][1].hwnd == 20


def test_main_mdi_frame_ranks_above_autocad_text_window():
    candidates = [
        WindowCandidate(
            hwnd=10,
            title="AutoCAD 文本窗口 - Drawing1.dwg",
            class_name="Afx:123:b:456",
            process_name="acad.exe",
        ),
        WindowCandidate(
            hwnd=20,
            title="Autodesk AutoCAD 2022 - [Drawing1.dwg]",
            class_name="AfxMDIFrame140u",
            process_name="acad.exe",
        ),
    ]
    ranked = rank_window_candidates(
        candidates,
        cad_type="autocad",
        document_name="Drawing1.dwg",
    )
    assert ranked[0][1].hwnd == 20


def test_image_quality_rejects_blank_capture_and_accepts_ui_pixels():
    blank = Image.new("RGB", (200, 100), "black")
    assert image_quality(blank)["valid"] is False
    image = Image.new("RGB", (200, 100), "black")
    for x in range(50, 150):
        for y in range(25, 75):
            image.putpixel((x, y), (220, 220, 220))
    assert image_quality(image)["valid"] is True


def test_background_window_never_uses_screen_crop_after_blank_printwindow():
    blank = Image.new("RGB", (200, 100), "black")
    with (
        patch("cad_vision.window_capture.win32gui.IsIconic", return_value=False),
        patch("cad_vision.window_capture.win32gui.GetWindowRect", return_value=(0, 0, 200, 100)),
        patch("cad_vision.window_capture.win32gui.IsWindowVisible", return_value=True),
        patch("cad_vision.window_capture.win32gui.GetForegroundWindow", return_value=999),
        patch("cad_vision.window_capture._print_window", return_value=blank),
        patch("cad_vision.window_capture.ImageGrab.grab") as screen_grab,
    ):
        with pytest.raises(RuntimeError, match="screen-crop fallback is unsafe"):
            capture_cad_window(123)
    screen_grab.assert_not_called()


def test_foreground_window_may_use_validated_screen_crop_fallback():
    blank = Image.new("RGB", (200, 100), "black")
    valid = Image.new("RGB", (200, 100), "black")
    for x in range(50, 150):
        for y in range(25, 75):
            valid.putpixel((x, y), (220, 220, 220))
    with (
        patch("cad_vision.window_capture.win32gui.IsIconic", return_value=False),
        patch("cad_vision.window_capture.win32gui.GetWindowRect", return_value=(0, 0, 200, 100)),
        patch("cad_vision.window_capture.win32gui.IsWindowVisible", return_value=True),
        patch("cad_vision.window_capture.win32gui.GetForegroundWindow", return_value=123),
        patch("cad_vision.window_capture._print_window", return_value=blank),
        patch("cad_vision.window_capture.ImageGrab.grab", return_value=valid),
    ):
        result = capture_cad_window(123)
    assert result["capture_method"] == "ImageGrab(screen crop)"
    assert result["quality"]["valid"] is True


def test_allow_restore_temporarily_activates_background_window_for_crop():
    blank = Image.new("RGB", (200, 100), "black")
    valid = Image.new("RGB", (200, 100), "black")
    for x in range(50, 150):
        for y in range(25, 75):
            valid.putpixel((x, y), (220, 220, 220))
    foreground = iter((999, 123))
    with (
        patch("cad_vision.window_capture.win32gui.IsIconic", return_value=False),
        patch("cad_vision.window_capture.win32gui.GetWindowRect", return_value=(0, 0, 200, 100)),
        patch("cad_vision.window_capture.win32gui.IsWindowVisible", return_value=True),
        patch(
            "cad_vision.window_capture.win32gui.GetForegroundWindow",
            side_effect=lambda: next(foreground),
        ),
        patch("cad_vision.window_capture._print_window", return_value=blank),
        patch("cad_vision.window_capture._activate_foreground_window", return_value=(999, True)),
        patch("cad_vision.window_capture._restore_foreground_window", return_value=True) as restore,
        patch("cad_vision.window_capture.ImageGrab.grab", return_value=valid),
    ):
        result = capture_cad_window(123, allow_restore=True)
    assert result["foreground_activated"] is True
    assert result["foreground_restored"] is True
    restore.assert_called_once_with(999, 123)


def test_allow_restore_rechecks_visibility_after_showing_hidden_window():
    blank = Image.new("RGB", (200, 100), "black")
    valid = Image.new("RGB", (200, 100), "black")
    valid.putpixel((50, 25), (220, 220, 220))
    valid.putpixel((150, 75), (220, 220, 220))
    foreground = iter((999, 123))
    visibility = iter((False, True))
    with (
        patch("cad_vision.window_capture.win32gui.IsIconic", return_value=False),
        patch("cad_vision.window_capture.win32gui.GetWindowRect", return_value=(0, 0, 200, 100)),
        patch(
            "cad_vision.window_capture.win32gui.IsWindowVisible",
            side_effect=lambda _hwnd: next(visibility),
        ),
        patch(
            "cad_vision.window_capture.win32gui.GetForegroundWindow",
            side_effect=lambda: next(foreground),
        ),
        patch("cad_vision.window_capture._print_window", return_value=blank),
        patch("cad_vision.window_capture._activate_foreground_window", return_value=(999, True)),
        patch("cad_vision.window_capture._restore_foreground_window", return_value=True),
        patch("cad_vision.window_capture.ImageGrab.grab", return_value=valid),
    ):
        result = capture_cad_window(123, allow_restore=True)
    assert result["quality"]["valid"] is True
    assert result["foreground_activated"] is True


def test_minimized_restore_waits_for_window_rectangle_to_become_valid():
    valid = Image.new("RGB", (200, 100), "black")
    valid.putpixel((50, 25), (220, 220, 220))
    valid.putpixel((150, 75), (220, 220, 220))
    with (
        patch(
            "cad_vision.window_capture.win32gui.IsIconic",
            side_effect=[True, False],
        ),
        patch("cad_vision.window_capture.win32gui.ShowWindow"),
        patch(
            "cad_vision.window_capture.win32gui.GetWindowRect",
            side_effect=[(-21333, -21333, -21175, -21307), (0, 0, 200, 100)],
        ),
        patch("cad_vision.window_capture.time.sleep"),
        patch("cad_vision.window_capture._print_window", return_value=valid),
    ):
        result = capture_cad_window(123, allow_restore=True)
    assert result["restored"] is True
    assert result["rect"] == [0, 0, 200, 100]


def test_invalid_virtual_minimized_rect_recovers_recorded_window_placement():
    valid = Image.new("RGB", (200, 100), "black")
    valid.putpixel((50, 25), (220, 220, 220))
    valid.putpixel((150, 75), (220, 220, 220))
    placement = (2, 3, (-1, -1), (-1, -1), (0, 73, 1704, 985))
    with (
        patch("cad_vision.window_capture.win32gui.IsIconic", return_value=False),
        patch(
            "cad_vision.window_capture.win32gui.GetWindowRect",
            side_effect=[(-21333, -21333, -21175, -21307), (0, 0, 200, 100)],
        ),
        patch("cad_vision.window_capture.win32gui.GetWindowPlacement", return_value=placement),
        patch("cad_vision.window_capture.win32gui.SetWindowPlacement") as set_placement,
        patch("cad_vision.window_capture.win32gui.ShowWindow"),
        patch("cad_vision.window_capture._print_window", return_value=valid),
    ):
        result = capture_cad_window(123, allow_restore=True)
    assert result["restored"] is True
    assert result["rect"] == [0, 0, 200, 100]
    set_placement.assert_called_once()
