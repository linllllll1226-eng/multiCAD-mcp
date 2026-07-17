"""Reliable Windows CAD window discovery and capture helpers."""

from __future__ import annotations

import ctypes
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import win32api
import win32con
import win32gui
import win32process
import win32ui
from PIL import Image, ImageGrab, ImageStat


@dataclass(frozen=True)
class WindowCandidate:
    """One top-level window considered during CAD discovery."""

    hwnd: int
    title: str
    class_name: str
    process_name: str = ""
    visible: bool = True
    iconic: bool = False


_PROCESS_NAMES = {
    "autocad": {"acad.exe"},
    "zwcad": {"zwcad.exe"},
    "gcad": {"gcad.exe", "gstarcad.exe"},
    "bricscad": {"bricscad.exe"},
}
_TITLE_TERMS = {
    "autocad": ("autocad", "autodesk"),
    "zwcad": ("zwcad", "中望"),
    "gcad": ("gstarcad", "浩辰"),
    "bricscad": ("bricscad",),
}
DEFAULT_WINDOW_CAPTURE_ROOT = (
    Path(__file__).resolve().parents[2] / "data" / "audit_reports" / "live"
)


def rank_window_candidates(
    candidates: list[WindowCandidate],
    *,
    cad_type: str,
    document_name: str = "",
    direct_hwnd: int = 0,
) -> list[tuple[int, WindowCandidate]]:
    """Rank windows using COM HWND, document title, process, class, and visibility."""
    cad_type = cad_type.lower()
    process_names = _PROCESS_NAMES.get(cad_type, set())
    title_terms = _TITLE_TERMS.get(cad_type, (cad_type,))
    document_name = document_name.casefold().strip()
    ranked = []
    for candidate in candidates:
        title = candidate.title.casefold()
        class_name = candidate.class_name.casefold()
        process_name = candidate.process_name.casefold()
        score = 0
        if direct_hwnd and candidate.hwnd == direct_hwnd:
            score += 1000
        if document_name and document_name in title:
            score += 120
        if process_name in process_names:
            score += 90
        if any(term in title for term in title_terms):
            score += 50
        if any(term in class_name for term in ("autocad", "acad", "afx:")):
            score += 25
        if "afxmdiframe" in class_name:
            score += 300
        if candidate.visible:
            score += 10
        if candidate.iconic:
            score -= 5
        if any(
            term in title
            for term in ("vba", "visual basic", "text window", "文本窗口", "command history")
        ):
            score -= 200
        if score > 0:
            ranked.append((score, candidate))
    return sorted(ranked, key=lambda item: (item[0], item[1].visible), reverse=True)


def _process_name(hwnd: int) -> str:
    try:
        _thread_id, process_id = win32process.GetWindowThreadProcessId(hwnd)
        access = win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ
        process = win32api.OpenProcess(access, False, process_id)
        try:
            return Path(win32process.GetModuleFileNameEx(process, 0)).name.lower()
        finally:
            win32api.CloseHandle(process)
    except Exception:
        return ""


def _application_hwnd(application: Any) -> int:
    for name in ("HWND", "HWnd", "Hwnd", "hwnd"):
        try:
            value = int(getattr(application, name))
            if value and win32gui.IsWindow(value):
                return value
        except Exception:
            continue
    return 0


def discover_cad_window(
    application: Any,
    *,
    cad_type: str,
    document_name: str = "",
) -> dict[str, Any]:
    """Find the real main CAD HWND without relying on one brittle class string."""
    direct_hwnd = _application_hwnd(application)
    candidates: list[WindowCandidate] = []

    def collect(hwnd: int, _extra: Any) -> None:
        try:
            title = win32gui.GetWindowText(hwnd)
            class_name = win32gui.GetClassName(hwnd)
            if not title and hwnd != direct_hwnd:
                return
            candidates.append(
                WindowCandidate(
                    hwnd=hwnd,
                    title=title,
                    class_name=class_name,
                    process_name=_process_name(hwnd),
                    visible=bool(win32gui.IsWindowVisible(hwnd)),
                    iconic=bool(win32gui.IsIconic(hwnd)),
                )
            )
        except Exception:
            return

    win32gui.EnumWindows(collect, None)
    if direct_hwnd and all(candidate.hwnd != direct_hwnd for candidate in candidates):
        collect(direct_hwnd, None)
    ranked = rank_window_candidates(
        candidates,
        cad_type=cad_type,
        document_name=document_name,
        direct_hwnd=direct_hwnd,
    )
    if not ranked or ranked[0][0] < 40:
        diagnostics = [
            {
                "hwnd": candidate.hwnd,
                "title": candidate.title,
                "class_name": candidate.class_name,
                "process_name": candidate.process_name,
            }
            for _score, candidate in ranked[:8]
        ]
        raise RuntimeError(f"No trustworthy CAD top-level window found; candidates={diagnostics}")
    score, selected = ranked[0]
    return {
        "hwnd": selected.hwnd,
        "title": selected.title,
        "class_name": selected.class_name,
        "process_name": selected.process_name,
        "visible": selected.visible,
        "minimized": selected.iconic,
        "discovery_score": score,
        "direct_com_hwnd": direct_hwnd,
    }


def image_quality(image: Image.Image) -> dict[str, Any]:
    """Detect all-black, all-white, or near-uniform capture failures."""
    gray = image.convert("L")
    stats = ImageStat.Stat(gray)
    extrema = gray.getextrema()
    standard_deviation = float(stats.stddev[0])
    mean = float(stats.mean[0])
    valid = bool(extrema[0] != extrema[1] and standard_deviation >= 2.0)
    return {
        "valid": valid,
        "mean_luma": round(mean, 3),
        "luma_stddev": round(standard_deviation, 3),
        "luma_extrema": list(extrema),
    }


def _print_window(hwnd: int, width: int, height: int) -> Image.Image:
    window_dc = win32gui.GetWindowDC(hwnd)
    if not window_dc:
        raise RuntimeError("GetWindowDC returned no device context")
    source_dc = win32ui.CreateDCFromHandle(window_dc)
    memory_dc = source_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    try:
        bitmap.CreateCompatibleBitmap(source_dc, width, height)
        memory_dc.SelectObject(bitmap)
        # PW_RENDERFULLCONTENT captures DirectComposition windows better on Windows 10/11.
        succeeded = ctypes.windll.user32.PrintWindow(hwnd, memory_dc.GetSafeHdc(), 2)
        if not succeeded:
            raise RuntimeError("PrintWindow returned failure")
        bits = bitmap.GetBitmapBits(True)
        image = Image.frombuffer("RGB", (width, height), bits, "raw", "BGRX", 0, 1)
        return image.copy()
    finally:
        try:
            win32gui.DeleteObject(bitmap.GetHandle())
        except Exception:
            pass
        memory_dc.DeleteDC()
        source_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, window_dc)


def _activate_foreground_window(hwnd: int) -> tuple[int, bool]:
    """Temporarily bring ``hwnd`` forward for a trustworthy screen crop."""
    previous = int(win32gui.GetForegroundWindow() or 0)
    if previous == int(hwnd):
        return previous, True
    current_thread = int(ctypes.windll.kernel32.GetCurrentThreadId())
    attached_threads: list[int] = []
    for window in (previous, int(hwnd)):
        if not window:
            continue
        try:
            thread_id, _process_id = win32process.GetWindowThreadProcessId(window)
            thread_id = int(thread_id)
            if thread_id != current_thread and thread_id not in attached_threads:
                if ctypes.windll.user32.AttachThreadInput(current_thread, thread_id, True):
                    attached_threads.append(thread_id)
        except Exception:
            continue
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        # The paired TOPMOST/NOTOPMOST transition avoids leaving CAD pinned
        # above other applications while overcoming the Windows foreground lock.
        win32gui.SetWindowPos(
            hwnd,
            win32con.HWND_TOPMOST,
            0,
            0,
            0,
            0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
        )
        win32gui.SetWindowPos(
            hwnd,
            win32con.HWND_NOTOPMOST,
            0,
            0,
            0,
            0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
        )
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    finally:
        for thread_id in reversed(attached_threads):
            try:
                ctypes.windll.user32.AttachThreadInput(current_thread, thread_id, False)
            except Exception:
                pass
    time.sleep(0.25)
    return previous, int(win32gui.GetForegroundWindow() or 0) == int(hwnd)


def _restore_foreground_window(previous: int, target: int) -> bool:
    """Best-effort restoration of the window focused before CAD capture."""
    if not previous or previous == int(target) or not win32gui.IsWindow(previous):
        return False
    current_thread = int(ctypes.windll.kernel32.GetCurrentThreadId())
    previous_thread = 0
    attached = False
    try:
        previous_thread, _process_id = win32process.GetWindowThreadProcessId(previous)
        previous_thread = int(previous_thread)
        if previous_thread != current_thread:
            attached = bool(
                ctypes.windll.user32.AttachThreadInput(current_thread, previous_thread, True)
            )
        win32gui.ShowWindow(previous, win32con.SW_SHOW)
        win32gui.BringWindowToTop(previous)
        win32gui.SetForegroundWindow(previous)
        return True
    except Exception:
        return False
    finally:
        if attached:
            try:
                ctypes.windll.user32.AttachThreadInput(current_thread, previous_thread, False)
            except Exception:
                pass


def _wait_for_valid_window_rect(
    hwnd: int, *, timeout: float = 1.5, interval: float = 0.05
) -> tuple[int, int, int, int]:
    """Wait briefly for Windows to publish a real rectangle after SW_RESTORE."""
    deadline = time.monotonic() + timeout
    last = tuple(int(value) for value in win32gui.GetWindowRect(hwnd))
    while last[2] - last[0] < 100 or last[3] - last[1] < 100:
        if time.monotonic() >= deadline:
            return last
        time.sleep(interval)
        last = tuple(int(value) for value in win32gui.GetWindowRect(hwnd))
    return last


def _restore_recorded_window_placement(hwnd: int) -> bool:
    """Recover a visible window stranded at Windows' virtual minimized coordinates."""
    try:
        flags, show_command, minimum, maximum, normal = win32gui.GetWindowPlacement(hwnd)
        left, top, right, bottom = (int(value) for value in normal)
        if right - left < 100 or bottom - top < 100:
            return False
        win32gui.SetWindowPlacement(
            hwnd,
            (flags, win32con.SW_SHOWNORMAL, minimum, maximum, normal),
        )
        if show_command == win32con.SW_SHOWMAXIMIZED:
            win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
        return True
    except Exception:
        return False


def capture_cad_window(hwnd: int, *, allow_restore: bool = False) -> dict[str, Any]:
    """Capture by HWND, preferring PrintWindow and validating fresh pixel content."""
    minimized = bool(win32gui.IsIconic(hwnd))
    restored = False
    if minimized and allow_restore:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        restored = True
        minimized = bool(win32gui.IsIconic(hwnd))
    if minimized:
        raise RuntimeError(
            "CAD window is minimized; use off-screen task audit or allow_restore=true"
        )
    if restored:
        left, top, right, bottom = _wait_for_valid_window_rect(hwnd)
    else:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    if (width < 100 or height < 100) and allow_restore:
        if _restore_recorded_window_placement(hwnd):
            restored = True
            left, top, right, bottom = _wait_for_valid_window_rect(hwnd)
            width, height = right - left, bottom - top
    if width < 100 or height < 100:
        raise RuntimeError(f"CAD window rectangle is invalid: {(left, top, right, bottom)}")
    attempts = []
    try:
        image = _print_window(hwnd, width, height)
        quality = image_quality(image)
        attempts.append({"method": "PrintWindow(PW_RENDERFULLCONTENT)", "quality": quality})
        if quality["valid"]:
            return {
                "image": image,
                "capture_method": "PrintWindow(PW_RENDERFULLCONTENT)",
                "quality": quality,
                "restored": restored,
                "rect": [left, top, right, bottom],
                "attempts": attempts,
            }
    except Exception as exc:
        attempts.append({"method": "PrintWindow(PW_RENDERFULLCONTENT)", "error": str(exc)})

    # A screen crop is only trustworthy while the target CAD window is the
    # foreground window.  When CAD is covered, hidden, or on another virtual
    # desktop, ImageGrab returns the pixels of the covering application.  Pixel
    # variance cannot distinguish that image from a real CAD capture, so never
    # report such a crop as success.
    foreground_hwnd = int(win32gui.GetForegroundWindow() or 0)
    target_visible = bool(win32gui.IsWindowVisible(hwnd))
    previous_foreground = foreground_hwnd
    foreground_activated = False
    if foreground_hwnd != int(hwnd) and allow_restore:
        previous_foreground, foreground_activated = _activate_foreground_window(hwnd)
        foreground_hwnd = int(win32gui.GetForegroundWindow() or 0)
        target_visible = bool(win32gui.IsWindowVisible(hwnd))
    if not target_visible or foreground_hwnd != int(hwnd):
        raise RuntimeError(
            "PrintWindow did not return valid CAD pixels and screen-crop fallback "
            "is unsafe while CAD is not the visible foreground window; use "
            "allow_restore=true for a temporary focus switch or "
            f"cad_render_task_audit instead. attempts={attempts}"
        )
    foreground_restored = False
    try:
        # Refresh coordinates after activation because restoring a minimized
        # window may change its rectangle.
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        image = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
        quality = image_quality(image)
        attempts.append({"method": "ImageGrab(screen crop)", "quality": quality})
        if not quality["valid"]:
            raise RuntimeError(
                f"All CAD window capture strategies returned invalid pixels: {attempts}"
            )
    finally:
        if foreground_activated:
            foreground_restored = _restore_foreground_window(previous_foreground, hwnd)
    return {
        "image": image,
        "capture_method": "ImageGrab(screen crop)",
        "quality": quality,
        "restored": restored,
        "foreground_activated": foreground_activated,
        "foreground_restored": foreground_restored,
        "rect": [left, top, right, bottom],
        "attempts": attempts,
    }


def capture_live_cad_window(
    *,
    cad_type: str = "autocad",
    document_name: str = "",
    allow_restore: bool = False,
    application: Any | None = None,
) -> dict[str, Any]:
    """Discover and capture a CAD UI even when no COM proxy is available."""
    window = discover_cad_window(
        application if application is not None else object(),
        cad_type=cad_type,
        document_name=document_name,
    )
    captured = capture_cad_window(int(window["hwnd"]), allow_restore=allow_restore)
    image = captured.pop("image")
    root = Path(
        os.environ.get("MULTICAD_AUDIT_OUTPUT_ROOT", str(DEFAULT_WINDOW_CAPTURE_ROOT.parent))
    )
    if str(root).startswith("\\\\"):
        raise ValueError("Network audit output roots are not allowed")
    output_dir = root.expanduser().resolve() / "live"
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = output_dir / f"cad_window_{timestamp}.png"
    image.save(path, format="PNG")
    return {
        "success": True,
        "path": str(path),
        "window": window,
        **captured,
        "fallback": "Use cad_render_task_audit when live UI pixels are unavailable",
    }
