"""Windows helpers used by the shell: the resizable frameless window (edge resizing, minimum size, no maximize,
per-monitor DPI, DWM corners and border colour), Explorer, single instance, message box.

Everything here is ctypes / stdlib only and degrades to a harmless no-op on non-Windows hosts, so the module can be
imported by the unit tests on any machine. The geometry (`pick_window_size`, `clamp_rect`, `rescaled_rect`,
`restored_rect`, `fitting_scales`, the stored bounds) is pure and tested without a window.
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import time
from ctypes import wintypes
from typing import Any, Callable, Mapping, Optional, Sequence
from urllib.parse import urlparse

log = logging.getLogger("media_forge.shell.winutil")

IS_WINDOWS = sys.platform == "win32"

CREATE_NO_WINDOW = 0x08000000
ERROR_ALREADY_EXISTS = 183

DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2
DWMWA_BORDER_COLOR = 34

SW_RESTORE = 9
SW_SHOW = 5

MB_OK = 0x0
MB_ICONERROR = 0x10
MB_ICONINFORMATION = 0x40

GWL_STYLE = -16
GWLP_WNDPROC = -4
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WM_SIZE = 0x0005
WM_GETMINMAXINFO = 0x0024
WM_WINDOWPOSCHANGING = 0x0046
WM_WINDOWPOSCHANGED = 0x0047
WM_STYLECHANGING = 0x007C
WM_NCDESTROY = 0x0082
WM_NCCALCSIZE = 0x0083
WM_NCLBUTTONDOWN = 0x00A1
WM_NCLBUTTONDBLCLK = 0x00A3
WM_SYSCOMMAND = 0x0112
WM_EXITSIZEMOVE = 0x0232
WM_DPICHANGED = 0x02E0
WM_DISPLAYCHANGE = 0x007E
WM_SETTINGCHANGE = 0x001A
SIZE_MINIMIZED = 1
SIZE_MAXIMIZED = 2
SC_MAXIMIZE = 0xF030
SC_RESTORE = 0xF120
HTCAPTION, HTTOP, HTTOPLEFT, HTTOPRIGHT = 2, 12, 13, 14
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x10, 0x20
MONITOR_DEFAULTTONEAREST = 2
MONITORINFOF_PRIMARY = 1
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
VK_LBUTTON, VK_RBUTTON, SM_SWAPBUTTON = 0x01, 0x02, 23
# A press on the page handed to the system (shell/bridge.py `window_drag_start`): part -> hit-test code. "move" is
# the title bar, the others the top border, which belongs to the page.
DRAG_PARTS = {"move": HTCAPTION, "top": HTTOP, "top-left": HTTOPLEFT, "top-right": HTTOPRIGHT}

if IS_WINDOWS:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _dwmapi = ctypes.WinDLL("dwmapi")

    _user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    _user32.FindWindowW.restype = wintypes.HWND
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.IsIconic.argtypes = [wintypes.HWND]
    _user32.IsIconic.restype = wintypes.BOOL
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    _user32.MessageBoxW.restype = ctypes.c_int

    _kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    _kernel32.ReleaseMutex.restype = wintypes.BOOL

    _dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, wintypes.LPCVOID, wintypes.DWORD]
    _dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long

    LRESULT = ctypes.c_ssize_t
    _WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    _user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    _user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    _user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetWindowLongPtrW.restype = ctypes.c_void_p
    _user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                        wintypes.LPARAM]
    _user32.CallWindowProcW.restype = LRESULT
    _user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, wintypes.UINT]
    _user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    _user32.MonitorFromWindow.restype = wintypes.HANDLE
    _user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    _user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    _user32.GetDpiForWindow.restype = wintypes.UINT
    _user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetWindowRect.restype = wintypes.BOOL
    _user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetClientRect.restype = wintypes.BOOL
    _user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    _user32.ClientToScreen.restype = wintypes.BOOL
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    _user32.ReleaseCapture.restype = wintypes.BOOL
    _user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _user32.GetAsyncKeyState.restype = ctypes.c_short
    _user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    _user32.IsIconic.argtypes = [wintypes.HWND]
    _MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
                                          ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    _user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, _MONITORENUMPROC, wintypes.LPARAM]
    try:
        _shcore = ctypes.WinDLL("shcore")
        _shcore.GetDpiForMonitor.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.POINTER(wintypes.UINT),
                                             ctypes.POINTER(wintypes.UINT)]
    except OSError:  # before Windows 8.1
        _shcore = None


# --- Explorer / shell --------------------------------------------------------------------------------------------

def reveal_in_explorer(path: str) -> bool:
    """Open Explorer with the file selected; a folder opens itself selected in its parent. False when missing."""
    if not path:
        return False
    path = os.path.normpath(path)
    if not os.path.exists(path):
        return False
    try:
        # One command-line string on purpose: explorer parses `/select,"<path>"` itself and rejects the argument
        # vector Python would build for a list. explorer.exe exits with 1 and is not waited for.
        subprocess.Popen(f'explorer /select,"{path}"', creationflags=CREATE_NO_WINDOW if IS_WINDOWS else 0,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        log.exception("reveal_in_explorer failed for %s", path)
        return False


def open_path(path: str) -> bool:
    """Open a file or folder with the shell default handler."""
    if not path or not os.path.exists(path):
        return False
    try:
        os.startfile(path)  # type: ignore[attr-defined]
        return True
    except (OSError, AttributeError):
        log.exception("open_path failed for %s", path)
        return False


def open_url(url: str) -> bool:
    """Open an https:// URL in the default browser. Anything else is refused."""
    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return False
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        return False
    try:
        os.startfile(parsed.geturl())  # type: ignore[attr-defined]
        return True
    except (OSError, AttributeError):
        log.exception("open_url failed for %s", url)
        return False


def message_box(title: str, text: str, error: bool = False) -> None:
    """Blocking native message box, for failures before a window exists (a windowed exe has no console)."""
    if not IS_WINDOWS:
        print(f"{title}: {text}", file=sys.stderr)
        return
    _user32.MessageBoxW(None, text, title, MB_OK | (MB_ICONERROR if error else MB_ICONINFORMATION))


# --- single instance ---------------------------------------------------------------------------------------------

WAIT_OBJECT_0 = 0
WAIT_ABANDONED = 0x80


def acquire_single_instance(name: str, timeout: float = 0.0):
    """Own the named mutex of the running instance. Returns its handle (keep it, and release it on the thread that
    acquired it), or None when another instance still owns it after `timeout` seconds. A mutex its owner released,
    or left behind by an instance that ended without releasing it (abandoned), is acquired."""
    if not IS_WINDOWS:
        return object()
    handle = _kernel32.CreateMutexW(None, True, name)
    err = ctypes.get_last_error()
    if not handle:
        return object()  # cannot decide; do not block the launch
    if err != ERROR_ALREADY_EXISTS:
        return handle  # created, and owned from the start
    result = _kernel32.WaitForSingleObject(handle, max(0, int(timeout * 1000)))
    if result in (WAIT_OBJECT_0, WAIT_ABANDONED):
        return handle
    _kernel32.CloseHandle(handle)
    return None


def release_single_instance(handle) -> None:
    """Release and close the mutex `acquire_single_instance` returned, on the thread that acquired it."""
    if IS_WINDOWS and isinstance(handle, int) and handle:
        try:
            _kernel32.ReleaseMutex(handle)
            _kernel32.CloseHandle(handle)
        except OSError:
            pass


def find_window(title: str) -> int:
    if not IS_WINDOWS:
        return 0
    return _user32.FindWindowW(None, title) or 0


def bring_window_to_front(title: str, attempts: int = 20, delay: float = 0.1) -> bool:
    """Restore and focus the top-level window with this exact title (retried while the first instance starts)."""
    for _ in range(max(1, attempts)):
        hwnd = find_window(title)
        if hwnd:
            _user32.ShowWindow(hwnd, SW_RESTORE if _user32.IsIconic(hwnd) else SW_SHOW)
            return bool(_user32.SetForegroundWindow(hwnd))
        time.sleep(delay)
    return False


# --- DWM ---------------------------------------------------------------------------------------------------------

def set_rounded_corners(hwnd: int) -> bool:
    """Windows 11 rounded corners for a frameless window. Harmless (returns False) on older systems."""
    if not IS_WINDOWS or not hwnd:
        return False
    value = ctypes.c_int(DWMWCP_ROUND)
    hr = _dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(value), ctypes.sizeof(value))
    return hr == 0


def colorref(hex_color: str) -> int:
    """'#rrggbb' -> COLORREF 0x00BBGGRR."""
    text = hex_color.lstrip("#")
    if len(text) != 6:
        raise ValueError(f"not a #rrggbb colour: {hex_color!r}")
    r, g, b = int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    return (b << 16) | (g << 8) | r


def set_border_color(hwnd: int, hex_color: str) -> bool:
    """Windows 11 window border colour (DWMWA_BORDER_COLOR); the page background hides the light frame line.
    Harmless (returns False) before Windows 11 build 22000."""
    if not IS_WINDOWS or not hwnd:
        return False
    value = wintypes.DWORD(colorref(hex_color))
    hr = _dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_BORDER_COLOR, ctypes.byref(value), ctypes.sizeof(value))
    return hr == 0


# --- window geometry (pure) --------------------------------------------------------------------------------------
#
# A rect is (x, y, w, h) of the window's client area in physical px of the virtual screen: the page covers the
# client area and the sizing border around it is invisible, so the client rect is what the user sees. A work area is
# (left, top, right, bottom) in physical px. A logical size is in the page's CSS px, the layout's own units:
# physical = logical x zoom (the Scale setting as a factor) x DPI scale of the monitor.

def pick_window_size(ladder: Sequence[tuple[int, int]], work: Optional[tuple[int, int]],
                     zoom: float = 1.0) -> tuple[int, int]:
    """The largest size of `ladder` (largest first, logical px) whose size times `zoom` (the Scale setting as a
    factor) fits the work area `work` (logical px); the smallest step when none fits, the first one when the work
    area is unknown."""
    if not ladder:
        raise ValueError("empty size ladder")
    if work is None:
        return ladder[0]
    for width, height in ladder:
        if width * zoom <= work[0] and height * zoom <= work[1]:
            return width, height
    return ladder[-1]


def scaled_size(size: tuple[float, float], factor: float) -> tuple[int, int]:
    """`size` times `factor`, rounded to whole px."""
    return int(round(size[0] * factor)), int(round(size[1] * factor))


def clamp_rect(rect: tuple[int, int, int, int], work: Optional[tuple[int, int, int, int]],
               minimum: tuple[int, int] = (0, 0)) -> tuple[int, int, int, int]:
    """`rect` with its size kept between `minimum` and the work area and then moved inside the work area. When even
    `minimum` is larger than the work area, the minimum wins and the window starts at the work area's top-left."""
    x, y, w, h = rect
    w, h = max(minimum[0], w), max(minimum[1], h)
    if work is None:
        return x, y, w, h
    left, top, right, bottom = work
    w = max(minimum[0], min(w, right - left))
    h = max(minimum[1], min(h, bottom - top))
    x = max(left, min(x, right - w))
    y = max(top, min(y, bottom - h))
    return x, y, w, h


def rescaled_rect(rect: tuple[int, int, int, int], ratio: float, work: Optional[tuple[int, int, int, int]],
                  minimum: tuple[int, int] = (0, 0)) -> tuple[int, int, int, int]:
    """`rect` with its size multiplied by `ratio` around the same centre, then clamped (`clamp_rect`)."""
    x, y, w, h = rect
    nw, nh = scaled_size((w, h), ratio)
    nx = (2 * x + w - nw) // 2
    ny = (2 * y + h - nh) // 2
    return clamp_rect((nx, ny, nw, nh), work, minimum)


def centered_rect(size: tuple[int, int], work: Optional[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    """(x, y, w, h) of a window of `size` centred on the work area (left, top, right, bottom), kept inside it when
    it fits; (0, 0, w, h) when the work area is unknown. Physical px."""
    w, h = size
    if work is None:
        return 0, 0, w, h
    x = work[0] + ((work[2] - work[0]) - w) // 2
    y = work[1] + ((work[3] - work[1]) - h) // 2
    return max(work[0], x), max(work[1], y), w, h


def scale_fits(minimum: tuple[int, int], zoom: float, dpi_scale: float,
               work: Optional[tuple[int, int, int, int]]) -> bool:
    """True when the minimum window (logical px) at `zoom` fits the work area of a monitor of `dpi_scale`."""
    if work is None:
        return True
    w, h = scaled_size(minimum, zoom * dpi_scale)
    return w <= work[2] - work[0] and h <= work[3] - work[1]


def fitting_scales(steps: Sequence[int], minimum: tuple[int, int], dpi_scale: float,
                   work: Optional[tuple[int, int, int, int]]) -> list[int]:
    """The Scale steps (percent, smallest first) whose minimum window fits the work area; the smallest step always,
    since the window needs one."""
    fitting = [step for step in steps if scale_fits(minimum, step / 100.0, dpi_scale, work)]
    if steps and steps[0] not in fitting:
        fitting.insert(0, steps[0])
    return fitting


def bounds_of(rect: tuple[int, int, int, int], zoom: float, dpi_scale: float) -> dict[str, int]:
    """The stored window bounds of a client rect: the top-left corner in physical px of the virtual screen and the
    size in logical px, independent of the Scale step and of the monitor's DPI."""
    x, y, w, h = rect
    factor = zoom * dpi_scale
    return {"x": int(x), "y": int(y), "width": int(round(w / factor)), "height": int(round(h / factor))}


def _overlap(rect: tuple[int, int, int, int], work: tuple[int, int, int, int]) -> int:
    x, y, w, h = rect
    dx = min(x + w, work[2]) - max(x, work[0])
    dy = min(y + h, work[3]) - max(y, work[1])
    return dx * dy if dx > 0 and dy > 0 else 0


def restored_rect(bounds: Mapping[str, int], monitors: Sequence[tuple[tuple[int, int, int, int], float, bool]],
                  zoom: float, minimum: tuple[int, int]) -> Optional[tuple[tuple[int, int, int, int], float]]:
    """Where stored `bounds` (`bounds_of`) open on the connected `monitors` ((work area, DPI scale, primary) each):
    on the monitor whose work area the stored rect overlaps most, clamped into that work area; centred on the
    primary monitor when it overlaps none (its monitor is gone). Returns (rect, DPI scale of that monitor), or None
    without monitors."""
    if not monitors:
        return None
    best = None
    for work, dpi_scale, _primary in monitors:
        rect = (int(bounds["x"]), int(bounds["y"])) + scaled_size((bounds["width"], bounds["height"]),
                                                                   zoom * dpi_scale)
        area = _overlap(rect, work)
        if area and (best is None or area > best[0]):
            best = (area, rect, work, dpi_scale)
    if best is None:
        work, dpi_scale, _primary = next((m for m in monitors if m[2]), monitors[0])
        rect = centered_rect(scaled_size((bounds["width"], bounds["height"]), zoom * dpi_scale), work)
    else:
        _area, rect, work, dpi_scale = best
    return clamp_rect(rect, work, scaled_size(minimum, zoom * dpi_scale)), dpi_scale


# --- monitors, DPI, zoom -----------------------------------------------------------------------------------------

class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


class _WINDOWPOS(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("hwndInsertAfter", wintypes.HWND), ("x", ctypes.c_int),
                ("y", ctypes.c_int), ("cx", ctypes.c_int), ("cy", ctypes.c_int), ("flags", wintypes.UINT)]


class _NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]


class _MINMAXINFO(ctypes.Structure):
    _fields_ = [("ptReserved", wintypes.POINT), ("ptMaxSize", wintypes.POINT), ("ptMaxPosition", wintypes.POINT),
                ("ptMinTrackSize", wintypes.POINT), ("ptMaxTrackSize", wintypes.POINT)]


class _STYLESTRUCT(ctypes.Structure):
    _fields_ = [("styleOld", wintypes.DWORD), ("styleNew", wintypes.DWORD)]


def set_per_monitor_dpi_awareness() -> bool:
    """Per-monitor DPI awareness (v2) for the process, before any window exists: a window moved to a monitor of
    another DPI gets WM_DPICHANGED and WebView2 renders at that monitor's density, instead of Windows stretching a
    bitmap of the system DPI. False when the awareness was set already (a manifest) or Windows is older than 10
    1703."""
    if not IS_WINDOWS:
        return False
    try:
        func = _user32.SetProcessDpiAwarenessContext
        func.argtypes = [ctypes.c_void_p]
        func.restype = wintypes.BOOL
        return bool(func(ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)))
    except (AttributeError, OSError):
        return False


def monitor_work_area(hwnd) -> Optional[tuple[int, int, int, int]]:
    """(left, top, right, bottom) of the work area of the monitor the window is on (physical px), or None."""
    if not IS_WINDOWS or not hwnd:
        return None
    monitor = _user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    if not monitor:
        return None
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if not _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    w = info.rcWork
    return w.left, w.top, w.right, w.bottom


def monitors() -> list[tuple[tuple[int, int, int, int], float, bool]]:
    """The connected monitors: (work area in physical px, DPI scale, primary) each."""
    if not IS_WINDOWS:
        return []
    found: list[tuple[tuple[int, int, int, int], float, bool]] = []

    def visit(hmon, _hdc, _rect, _data):
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if _user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            dpi = 96
            if _shcore is not None:
                dx, dy = wintypes.UINT(), wintypes.UINT()
                if _shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dx), ctypes.byref(dy)) == 0 and dx.value:
                    dpi = dx.value
            w = info.rcWork
            found.append(((w.left, w.top, w.right, w.bottom), dpi / 96.0, bool(info.dwFlags & MONITORINFOF_PRIMARY)))
        return True

    _user32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(visit), 0)
    return found


def window_scale(hwnd) -> float:
    """DPI scale of the window (1.0 at 96 dpi)."""
    if not IS_WINDOWS or not hwnd:
        return 1.0
    try:
        return (_user32.GetDpiForWindow(hwnd) or 96) / 96.0
    except (AttributeError, OSError):
        return 1.0


def set_zoom_factor(window: Any, factor: float) -> bool:
    """The WebView2 zoom factor of a pywebview window (edgechromium backend): `window.native.webview` is the
    WinForms WebView2 control. Zoom is a device-scale change: the page keeps its logical viewport and text stays
    crisp. Call it on the UI thread. False when the control is not reachable."""
    native = getattr(window, "native", None)
    control = getattr(native, "webview", None)
    if control is None:
        return False
    try:
        control.ZoomFactor = float(factor)
        return True
    except Exception:  # noqa: BLE001 - a backend without the property
        log.exception("zoom factor %.2f could not be set", factor)
        return False


# --- resizable frameless window ----------------------------------------------------------------------------------
#
# A frameless pywebview form (WinForms FormBorderStyle.None) has no sizing border, and the WebView2 child window
# covers the whole client area, so the form never sees a mouse message over the page. `WindowHook` subclasses the
# form's window procedure:
# - WS_THICKFRAME gives back the system's sizing border; WM_NCCALCSIZE keeps it on the left, right and bottom, where
#   DWM draws it invisible outside the visible window (system cursors, the usual grip width), and drops it at the
#   top, so the page reaches the window's top edge. The top edge is resized from the page: a thin grip along the
#   title bar calls `begin_drag`, which starts the system's sizing loop for the top border. The title bar moves the
#   window the same way (the system's move loop), so a move is exact at every Scale and DPI and ends with
#   WM_EXITSIZEMOVE like a resize.
# - WM_GETMINMAXINFO and WM_WINDOWPOSCHANGING hold the minimum size (logical px x zoom x DPI); the restore after a
#   minimize keeps the window size (WinForms re-applies the client size of a borderless form as its window size).
# - No maximize by any route: WS_MAXIMIZEBOX stays cleared (also against WinForms restyling, WM_STYLECHANGING),
#   SC_MAXIMIZE and non-client double clicks are swallowed, a maximized state that still arrives
#   (ShowWindow(SW_MAXIMIZE) from outside) is undone with SC_RESTORE. WS_MINIMIZEBOX is set (taskbar click,
#   Win+Down).
# - WM_DPICHANGED (per-monitor DPI awareness): the client size is multiplied by the ratio of the DPIs at the
#   position Windows suggests, so the logical size stays and WebView2 renders at the new density.

_NOT_CALLED = object()


class WindowHook:
    """Window-procedure subclass of the resizable frameless window; keep the instance alive as long as the window
    exists. Runs on the UI thread. `minimum` is the minimum page size in logical px; `zoom` the Scale factor."""

    def __init__(self, hwnd: int, minimum: tuple[int, int]) -> None:
        self.hwnd = hwnd
        self.minimum = minimum
        self.zoom = 1.0
        self.on_size_end: Optional[Callable[[], None]] = None  # an edge drag or a keyboard size / move ended
        self.on_display: Optional[Callable[[], None]] = None   # another monitor, DPI or work area
        self._dpi = 96
        self._monitor = None
        self._normal_size: Optional[tuple[int, int]] = None    # window size of the restored window (physical px)
        self._restoring = False                                # minimized, until the restore's WM_SIZE ran
        self._prev_proc = None
        self._proc = None
        self.installed = False

    # -- setup
    def install(self) -> bool:
        if not IS_WINDOWS or not self.hwnd or self.installed:
            return self.installed
        self._dpi = _user32.GetDpiForWindow(self.hwnd) or 96
        self._monitor = _user32.MonitorFromWindow(self.hwnd, MONITOR_DEFAULTTONEAREST)
        style = _user32.GetWindowLongPtrW(self.hwnd, GWL_STYLE) or 0
        _user32.SetWindowLongPtrW(self.hwnd, GWL_STYLE, self._fixed_style(style))
        self._proc = _WNDPROC(self._wnd_proc)
        self._prev_proc = _user32.SetWindowLongPtrW(self.hwnd, GWLP_WNDPROC,
                                                    ctypes.cast(self._proc, ctypes.c_void_p).value)
        self.installed = bool(self._prev_proc)
        _user32.SetWindowPos(self.hwnd, None, 0, 0, 0, 0,
                             SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
        return self.installed

    @staticmethod
    def _fixed_style(style: int) -> int:
        return (int(style) | WS_THICKFRAME | WS_MINIMIZEBOX) & ~WS_MAXIMIZEBOX & 0xFFFFFFFF

    # -- geometry of the live window
    def client_rect(self) -> Optional[tuple[int, int, int, int]]:
        """The client area (what the user sees) in physical screen px, or None."""
        if not IS_WINDOWS or not self.hwnd:
            return None
        inner = wintypes.RECT()
        origin = wintypes.POINT(0, 0)
        if not _user32.GetClientRect(self.hwnd, ctypes.byref(inner)):
            return None
        _user32.ClientToScreen(self.hwnd, ctypes.byref(origin))
        return origin.x, origin.y, inner.right, inner.bottom

    def insets(self) -> tuple[int, int, int, int]:
        """(left, top, right, bottom) of the invisible sizing border: window rect minus client rect."""
        client = self.client_rect()
        outer = wintypes.RECT()
        if client is None or not _user32.GetWindowRect(self.hwnd, ctypes.byref(outer)):
            return 0, 0, 0, 0
        x, y, w, h = client
        return x - outer.left, y - outer.top, outer.right - (x + w), outer.bottom - (y + h)

    def scale(self) -> float:
        return window_scale(self.hwnd)

    def work_area(self) -> Optional[tuple[int, int, int, int]]:
        return monitor_work_area(self.hwnd)

    def min_client(self, zoom: Optional[float] = None) -> tuple[int, int]:
        """The minimum client size in physical px at `zoom` (default: the current zoom) and the window's DPI."""
        return scaled_size(self.minimum, (self.zoom if zoom is None else zoom) * self.scale())

    def is_minimized(self) -> bool:
        return bool(IS_WINDOWS and self.hwnd and _user32.IsIconic(self.hwnd))

    def place(self, rect: tuple[int, int, int, int],
              apply: Optional[Callable[[int, int, int, int], None]] = None) -> tuple[int, int, int, int]:
        """Move and size the window so its client area is `rect`. `apply(x, y, w, h)` performs the move of the
        window rect (pass the form's SetBounds so WinForms keeps its bounds in sync); the default is SetWindowPos.
        Returns the window rect (x, y, w, h) that was applied."""
        left, top, right, bottom = self.insets()
        x, y, w, h = rect
        outer = (x - left, y - top, w + left + right, h + top + bottom)
        if apply is not None:
            apply(*outer)
        elif IS_WINDOWS and self.hwnd:
            _user32.SetWindowPos(self.hwnd, None, *outer, SWP_NOZORDER | SWP_NOACTIVATE)
        return outer

    def begin_drag(self, part: str) -> bool:
        """Start the system's move loop ("move") or its sizing loop for the top border (the other DRAG_PARTS) while
        the primary mouse button is down on the page. The system loop moves the window in physical px at any Scale
        and DPI, carries it onto another monitor (WM_DPICHANGED) and ends with WM_EXITSIZEMOVE. Call it on the UI
        thread; returns whether the loop was started (False once the button is up again)."""
        code = DRAG_PARTS.get(part)
        if code is None or not IS_WINDOWS or not self.hwnd or not self.installed:
            return False
        button = VK_RBUTTON if _user32.GetSystemMetrics(SM_SWAPBUTTON) else VK_LBUTTON
        if not _user32.GetAsyncKeyState(button) & 0x8000:
            return False  # a short click: the button is up before the bridge call arrived, so there is no drag
        point = wintypes.POINT()
        _user32.GetCursorPos(ctypes.byref(point))
        _user32.ReleaseCapture()
        lparam = ((point.y & 0xFFFF) << 16) | (point.x & 0xFFFF)
        return bool(_user32.PostMessageW(self.hwnd, WM_NCLBUTTONDOWN, code, lparam))

    # -- window procedure
    def _call(self, hwnd, msg, wparam, lparam):
        return _user32.CallWindowProcW(self._prev_proc, hwnd, msg, wparam, lparam)

    def _notify(self, callback: Optional[Callable[[], None]]) -> None:
        if callback is not None:
            try:
                callback()
            except Exception:  # noqa: BLE001 - a listener must never break the message loop
                log.exception("window listener failed")

    def _on_pos_changing(self, hwnd, lparam) -> None:
        pos = ctypes.cast(lparam, ctypes.POINTER(_WINDOWPOS)).contents
        if pos.flags & SWP_NOSIZE or pos.x <= -32000 or pos.y <= -32000:
            return
        if self._restoring or _user32.IsIconic(hwnd):
            if self._normal_size is not None:
                pos.cx, pos.cy = self._normal_size
            return
        left, top, right, bottom = self.insets()
        if self._normal_size is not None and (pos.cx, pos.cy) == (self._normal_size[0] - left - right,
                                                                  self._normal_size[1] - top - bottom):
            # WinForms re-applies the client size it keeps as the window size of a borderless form (it does not
            # know about the sizing border this hook added): keep the window size instead.
            pos.cx, pos.cy = self._normal_size
            return
        min_w, min_h = self.min_client()
        pos.cx = max(pos.cx, min_w + left + right)
        pos.cy = max(pos.cy, min_h + top + bottom)

    def _on_dpi_changed(self, hwnd, wparam, lparam) -> None:
        new_dpi = int(wparam) & 0xFFFF or 96
        old_dpi = self._dpi or 96
        self._dpi = new_dpi
        suggested = ctypes.cast(lparam, ctypes.POINTER(wintypes.RECT)).contents
        client = self.client_rect()
        if client is None:
            return
        ratio = new_dpi / old_dpi
        width, height = scaled_size(client[2:], ratio)
        left, top, right, bottom = (int(round(v * ratio)) for v in self.insets())
        flags = SWP_NOZORDER | SWP_NOACTIVATE
        _user32.SetWindowPos(hwnd, None, suggested.left, suggested.top, width + left + right,
                             height + top + bottom, flags)
        after = self.client_rect()
        if after is not None and after[2:] != (width, height):  # the border at the new DPI was estimated
            outer = wintypes.RECT()
            _user32.GetWindowRect(hwnd, ctypes.byref(outer))
            _user32.SetWindowPos(hwnd, None, 0, 0, (outer.right - outer.left) + width - after[2],
                                 (outer.bottom - outer.top) + height - after[3], flags | SWP_NOMOVE)

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        result = _NOT_CALLED  # the original procedure's result once a branch has called it
        try:
            if msg == WM_NCCALCSIZE and wparam:
                params = ctypes.cast(lparam, ctypes.POINTER(_NCCALCSIZE_PARAMS)).contents
                original_top = params.rgrc[0].top
                result = self._call(hwnd, msg, wparam, lparam)
                params.rgrc[0].top = original_top  # no top border: the page owns the top edge
                return result
            if msg == WM_GETMINMAXINFO:
                result = self._call(hwnd, msg, wparam, lparam)
                info = ctypes.cast(lparam, ctypes.POINTER(_MINMAXINFO)).contents
                min_w, min_h = self.min_client()
                left, top, right, bottom = self.insets()
                info.ptMinTrackSize.x = min_w + left + right
                info.ptMinTrackSize.y = min_h + top + bottom
                return result
            if msg == WM_WINDOWPOSCHANGING:
                self._on_pos_changing(hwnd, lparam)
            elif msg == WM_WINDOWPOSCHANGED:
                pos = ctypes.cast(lparam, ctypes.POINTER(_WINDOWPOS)).contents
                if not (pos.flags & SWP_NOSIZE) and pos.x > -32000 and not self._restoring \
                        and not _user32.IsIconic(hwnd):
                    self._normal_size = (pos.cx, pos.cy)
                result = self._call(hwnd, msg, wparam, lparam)
                if not (pos.flags & SWP_NOMOVE) and not _user32.IsIconic(hwnd):
                    monitor = _user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
                    if monitor != self._monitor:
                        self._monitor = monitor
                        self._notify(self.on_display)
                return result
            elif msg == WM_SIZE:
                if int(wparam) == SIZE_MINIMIZED:
                    self._restoring = True
                    return self._call(hwnd, msg, wparam, lparam)
                result = self._call(hwnd, msg, wparam, lparam)
                self._restoring = False  # WinForms' own restore handling ran inside the call above
                if int(wparam) == SIZE_MAXIMIZED:
                    _user32.PostMessageW(hwnd, WM_SYSCOMMAND, SC_RESTORE, 0)
                return result
            elif msg == WM_SYSCOMMAND and (int(wparam) & 0xFFF0) == SC_MAXIMIZE:
                return 0  # system menu, keyboard, a double click on a caption
            elif msg == WM_NCLBUTTONDBLCLK:
                return 0  # a double click on the sizing border would maximize vertically
            elif msg == WM_STYLECHANGING and ctypes.c_int(int(wparam) & 0xFFFFFFFF).value == GWL_STYLE:
                change = ctypes.cast(lparam, ctypes.POINTER(_STYLESTRUCT)).contents
                change.styleNew = self._fixed_style(change.styleNew)
            elif msg == WM_EXITSIZEMOVE:
                result = self._call(hwnd, msg, wparam, lparam)
                self._notify(self.on_size_end)
                return result
            elif msg == WM_DPICHANGED:
                self._on_dpi_changed(hwnd, wparam, lparam)
                self._notify(self.on_display)
                return 0
            elif msg in (WM_DISPLAYCHANGE, WM_SETTINGCHANGE):
                result = self._call(hwnd, msg, wparam, lparam)
                self._notify(self.on_display)
                return result
            elif msg == WM_NCDESTROY:
                result = self._call(hwnd, msg, wparam, lparam)
                self.installed = False
                return result
        except Exception:  # noqa: BLE001 - never let a callback exception escape into the message loop
            log.debug("window procedure hook failed for message 0x%04x", msg, exc_info=True)
            if result is not _NOT_CALLED:
                return result  # the message was handled once already; a second call would apply it twice
        return self._call(hwnd, msg, wparam, lparam)
