"""Windows helpers used by the shell: frameless window with a locked 3:4 aspect, DWM corners, Explorer, single
instance, message box.

Everything here is ctypes / stdlib only and degrades to a harmless no-op on non-Windows hosts, so the module can be
imported by the unit tests on any machine. The size arithmetic (`aspect_sizing_rect`, `fit_aspect_box`,
`fit_start_client`) is pure and tested without a window.
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import time
from ctypes import wintypes
from typing import Callable, Optional
from urllib.parse import urlparse

log = logging.getLogger("media_forge.shell.winutil")

IS_WINDOWS = sys.platform == "win32"

CREATE_NO_WINDOW = 0x08000000
ERROR_ALREADY_EXISTS = 183

DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2

SW_RESTORE = 9
SW_SHOW = 5

MB_OK = 0x0
MB_ICONERROR = 0x10
MB_ICONINFORMATION = 0x40

# Aspect ratio of the client area (the page viewport): width : height = 3 : 4.
ASPECT_W = 3
ASPECT_H = 4

# WM_SIZING edges (wParam)
WMSZ_LEFT, WMSZ_RIGHT, WMSZ_TOP, WMSZ_TOPLEFT = 1, 2, 3, 4
WMSZ_TOPRIGHT, WMSZ_BOTTOM, WMSZ_BOTTOMLEFT, WMSZ_BOTTOMRIGHT = 5, 6, 7, 8

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

    _dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, wintypes.LPCVOID, wintypes.DWORD]
    _dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long


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

def acquire_single_instance(name: str):
    """Create the named mutex. Returns its handle (keep it alive), or None when another instance already owns it."""
    if not IS_WINDOWS:
        return object()
    handle = _kernel32.CreateMutexW(None, False, name)
    err = ctypes.get_last_error()
    if not handle:
        return object()  # cannot decide; do not block the launch
    if err == ERROR_ALREADY_EXISTS:
        _kernel32.CloseHandle(handle)
        return None
    return handle


def release_single_instance(handle) -> None:
    if IS_WINDOWS and isinstance(handle, int) and handle:
        try:
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


# --- aspect arithmetic (pure) ------------------------------------------------------------------------------------

def height_for_width(width: int) -> int:
    return int(round(width * ASPECT_H / ASPECT_W))


def width_for_height(height: int) -> int:
    return int(round(height * ASPECT_W / ASPECT_H))


def fit_aspect_box(box_w: int, box_h: int) -> tuple[int, int]:
    """The largest 3:4 client size that fits into box_w x box_h."""
    w = max(1, int(box_w))
    h = height_for_width(w)
    if h > box_h:
        h = max(1, int(box_h))
        w = width_for_height(h)
    return w, h


def fit_start_client(start: tuple[int, int], minimum: tuple[int, int],
                     work: Optional[tuple[int, int]], nc: tuple[int, int] = (0, 0)) -> tuple[int, int]:
    """Start client size (logical px): `start` scaled down, aspect kept, to fit the work area minus the frame `nc`.

    The minimum applies unless the work area itself is smaller; then the window just fits the work area.
    """
    w, h = int(start[0]), height_for_width(int(start[0]))
    if work is None:
        return w, h
    max_w, max_h = int(work[0]) - nc[0], int(work[1]) - nc[1]
    if w > max_w or h > max_h:
        w, h = fit_aspect_box(min(w, max_w), min(h, max_h))
    min_w, min_h = minimum
    if w < min_w and min_w <= max_w and min_h <= max_h:
        w, h = min_w, height_for_width(min_w)
    return w, h


def aspect_sizing_rect(rect: tuple[int, int, int, int], edge: int, nc: tuple[int, int] = (0, 0),
                       min_client: tuple[int, int] = (0, 0), max_client: Optional[tuple[int, int]] = None,
                       prev: Optional[tuple[int, int, int, int]] = None) -> tuple[int, int, int, int]:
    """The window rect that WM_SIZING should use so the client area keeps the 3:4 aspect.

    `rect` = (left, top, right, bottom) proposed by the system for a drag of `edge` (WMSZ_*), physical pixels;
    `nc` = window size minus client size (the invisible resize border); `min_client` / `max_client` limit the client
    size (physical pixels); `prev` = the rect before this drag step, used to decide which dimension a corner drag
    changes. The dragged edge wins: a side edge sets the width, a top / bottom edge sets the height, a corner sets
    the dimension that moved more. The edges opposite to the dragged one stay where they are.
    """
    left, top, right, bottom = (int(v) for v in rect)
    ncw, nch = nc
    cw, ch = (right - left) - ncw, (bottom - top) - nch

    if edge in (WMSZ_LEFT, WMSZ_RIGHT):
        width_wins = True
    elif edge in (WMSZ_TOP, WMSZ_BOTTOM):
        width_wins = False
    else:
        if prev is not None:
            pw, ph = (prev[2] - prev[0]) - ncw, (prev[3] - prev[1]) - nch
            # compare the relative changes in the aspect's units
            width_wins = abs(cw - pw) * ASPECT_H >= abs(ch - ph) * ASPECT_W
        else:
            width_wins = cw * ASPECT_H >= ch * ASPECT_W  # the larger of the two candidates
    if width_wins:
        ch = height_for_width(cw)
    else:
        cw = width_for_height(ch)

    min_w, min_h = min_client
    if min_w or min_h:
        if cw < min_w or ch < min_h:
            cw = max(min_w, width_for_height(min_h))
            ch = height_for_width(cw)
    if max_client is not None:
        max_w, max_h = max_client
        if cw > max_w or ch > max_h:
            cw, ch = fit_aspect_box(min(cw, max_w), min(ch, max_h))

    new_w, new_h = cw + ncw, ch + nch
    # anchor: the edges opposite to the dragged one stay
    if edge in (WMSZ_LEFT, WMSZ_TOPLEFT, WMSZ_BOTTOMLEFT):
        left = right - new_w
    else:
        right = left + new_w
    if edge in (WMSZ_TOP, WMSZ_TOPLEFT, WMSZ_TOPRIGHT):
        top = bottom - new_h
    else:
        bottom = top + new_h
    return left, top, right, bottom


# --- frameless window: edge resizing with the aspect lock --------------------------------------------------------
#
# A frameless pywebview window (WinForms FormBorderStyle.None) has no sizing border. `FramelessResizeHook` gives the
# form back WS_THICKFRAME (the system then provides the invisible resize border, hit-testing and the DWM shadow),
# keeps the whole window as client area except that border (WM_NCCALCSIZE), enforces the minimum size
# (WM_GETMINMAXINFO), locks the 3:4 aspect while an edge is dragged (WM_SIZING) and for any other size change
# (WM_WINDOWPOSCHANGING: snap, keyboard), and never maximizes (no WS_MAXIMIZEBOX, SC_MAXIMIZE and non-client
# double-clicks swallowed).

GWL_STYLE = -16
GWLP_WNDPROC = -4
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WM_SIZING = 0x0214
WM_NCCALCSIZE = 0x0083
WM_NCDESTROY = 0x0082
WM_GETMINMAXINFO = 0x0024
WM_WINDOWPOSCHANGING = 0x0046
WM_WINDOWPOSCHANGED = 0x0047
WM_SIZE = 0x0005
SIZE_MINIMIZED = 1
WM_SYSCOMMAND = 0x0112
WM_NCLBUTTONDBLCLK = 0x00A3
WM_NCHITTEST = 0x0084
WM_EXITSIZEMOVE = 0x0232
HTCLIENT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 1, 12, 13, 14
SC_MAXIMIZE = 0xF030
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x10, 0x20
MONITOR_DEFAULTTONEAREST = 2

if IS_WINDOWS:
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
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    _user32.MonitorFromWindow.restype = wintypes.HANDLE
    _user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    _user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    _user32.GetDpiForWindow.restype = wintypes.UINT


class _NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]


class _MINMAXINFO(ctypes.Structure):
    _fields_ = [
        ("ptReserved", wintypes.POINT),
        ("ptMaxSize", wintypes.POINT),
        ("ptMaxPosition", wintypes.POINT),
        ("ptMinTrackSize", wintypes.POINT),
        ("ptMaxTrackSize", wintypes.POINT),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


class _WINDOWPOS(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("hwndInsertAfter", wintypes.HWND), ("x", ctypes.c_int),
                ("y", ctypes.c_int), ("cx", ctypes.c_int), ("cy", ctypes.c_int), ("flags", wintypes.UINT)]


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


def window_scale(hwnd) -> float:
    """DPI scale of the window (1.0 at 96 dpi)."""
    if not IS_WINDOWS or not hwnd:
        return 1.0
    try:
        return (_user32.GetDpiForWindow(hwnd) or 96) / 96.0
    except (AttributeError, OSError):
        return 1.0


class FramelessResizeHook:
    """Subclass of the form's window procedure; keep the instance alive as long as the window exists."""

    def __init__(self, hwnd: int, min_client_size: tuple[int, int] = (0, 0), top_grip: int = 0) -> None:
        self.hwnd = hwnd
        self.min_client_size = min_client_size  # logical pixels (of the page viewport)
        # Height in physical pixels of a strip at the top of the client area that the page does not cover (the
        # form's top Padding): the WebView2 child window receives every mouse message over the page, so the top
        # edge can be resized only through such a strip of the form itself.
        self.top_grip = max(0, int(top_grip))
        self._prev_proc = None
        self._proc = None
        self._nc = (0, 0)  # window size minus client size (physical px)
        self._last_rect: Optional[tuple[int, int, int, int]] = None
        self._normal_size: Optional[tuple[int, int]] = None  # window size while neither minimized nor restoring
        self._restoring = False  # minimized, until the WM_SIZE of the restore has been handled
        self.installed = False

    # -- setup
    def install(self) -> bool:
        if not IS_WINDOWS or not self.hwnd or self.installed:
            return self.installed
        style = _user32.GetWindowLongPtrW(self.hwnd, GWL_STYLE) or 0
        style = (style | WS_THICKFRAME | WS_MINIMIZEBOX) & ~WS_MAXIMIZEBOX
        _user32.SetWindowLongPtrW(self.hwnd, GWL_STYLE, style)
        self._proc = _WNDPROC(self._wnd_proc)
        self._prev_proc = _user32.SetWindowLongPtrW(self.hwnd, GWLP_WNDPROC,
                                                    ctypes.cast(self._proc, ctypes.c_void_p).value)
        flags = SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED
        _user32.SetWindowPos(self.hwnd, None, 0, 0, 0, 0, flags)
        self._nc = self._measure_nc()
        self.installed = bool(self._prev_proc)
        return self.installed

    def _measure_nc(self) -> tuple[int, int]:
        outer, inner = wintypes.RECT(), wintypes.RECT()
        _user32.GetWindowRect(self.hwnd, ctypes.byref(outer))
        _user32.GetClientRect(self.hwnd, ctypes.byref(inner))
        # the top grip belongs to the client area but not to the page viewport: count it as frame
        return ((outer.right - outer.left) - (inner.right - inner.left),
                (outer.bottom - outer.top) - (inner.bottom - inner.top) + self.top_grip)

    @property
    def nc(self) -> tuple[int, int]:
        return self._nc

    def _min_client_physical(self) -> tuple[int, int]:
        scale = window_scale(self.hwnd)
        min_w, min_h = self.min_client_size
        if not (min_w and min_h):
            return 0, 0
        w = int(round(min_w * scale))
        return w, height_for_width(w)

    def _max_client_physical(self) -> Optional[tuple[int, int]]:
        work = monitor_work_area(self.hwnd)
        if work is None:
            return None
        return (work[2] - work[0]) - self._nc[0], (work[3] - work[1]) - self._nc[1]

    def start_client_size(self, start: tuple[int, int], minimum: tuple[int, int]) -> tuple[int, int]:
        """Logical start client size fitted to the current monitor's work area."""
        scale = window_scale(self.hwnd)
        work = monitor_work_area(self.hwnd)
        work_logical = None
        if work is not None:
            work_logical = (int((work[2] - work[0]) / scale), int((work[3] - work[1]) / scale))
        nc_logical = (int(round(self._nc[0] / scale)), int(round(self._nc[1] / scale)))
        return fit_start_client(start, minimum, work_logical, nc_logical)

    def set_client_size(self, width: int, height: int, apply: Optional[Callable[[int, int, int, int], None]] = None,
                        center_on_work_area: bool = True) -> tuple[int, int, int, int]:
        """Resize so the page viewport is `width` logical pixels wide with the 3:4 aspect (`height` is derived from
        the width in physical pixels so it matches the lock exactly), centred on the work area (or on the current
        window centre). `apply(x, y, w, h)` performs the move (pass the form's SetBounds so WinForms keeps its own
        bounds in sync); the default is SetWindowPos. Returns the physical window rect (x, y, w, h)."""
        if not self.installed:
            return (0, 0, 0, 0)
        scale = window_scale(self.hwnd)
        cw = int(round(width * scale))
        ch = height_for_width(cw)
        del height  # derived from the width so the lock holds exactly in physical pixels
        new_w, new_h = cw + self._nc[0], ch + self._nc[1]
        work = monitor_work_area(self.hwnd)
        rect = wintypes.RECT()
        _user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
        if center_on_work_area and work is not None:
            x = work[0] + ((work[2] - work[0]) - new_w) // 2
            y = work[1] + ((work[3] - work[1]) - new_h) // 2
        else:
            x = rect.left + ((rect.right - rect.left) - new_w) // 2
            y = rect.top + ((rect.bottom - rect.top) - new_h) // 2
        if work is not None:
            x = max(work[0], min(x, work[2] - new_w))
            y = max(work[1], min(y, work[3] - new_h))
        if apply is not None:
            apply(x, y, new_w, new_h)
        else:
            _user32.SetWindowPos(self.hwnd, None, x, y, new_w, new_h, SWP_NOZORDER | SWP_NOACTIVATE)
        return x, y, new_w, new_h

    # -- window procedure
    def _on_sizing(self, wparam, lparam) -> None:
        r = ctypes.cast(lparam, ctypes.POINTER(wintypes.RECT)).contents
        proposed = (r.left, r.top, r.right, r.bottom)
        if self._last_rect is None:
            current = wintypes.RECT()
            _user32.GetWindowRect(self.hwnd, ctypes.byref(current))
            self._last_rect = (current.left, current.top, current.right, current.bottom)
        new = aspect_sizing_rect(proposed, int(wparam), self._nc, self._min_client_physical(),
                                 self._max_client_physical(), prev=self._last_rect)
        r.left, r.top, r.right, r.bottom = new
        self._last_rect = new

    def _client_of(self, window_size: tuple[int, int]) -> tuple[int, int]:
        """Client-rect size of a window of `window_size` (the top grip belongs to the client rect)."""
        return window_size[0] - self._nc[0], window_size[1] - (self._nc[1] - self.top_grip)

    def _on_pos_changing(self, lparam) -> None:
        pos = ctypes.cast(lparam, ctypes.POINTER(_WINDOWPOS)).contents
        if pos.flags & SWP_NOSIZE or pos.x <= -32000 or pos.y <= -32000:
            return
        if self._restoring or _user32.IsIconic(self.hwnd):
            # Leaving the minimized state: WinForms restores its own idea of the bounds (the client size of a
            # borderless form, i.e. without the frame this hook added); keep the size the window had instead.
            if self._normal_size is not None:
                pos.cx, pos.cy = self._normal_size
            return
        cw, ch = pos.cx - self._nc[0], pos.cy - self._nc[1]
        if cw <= 0 or ch <= 0:
            return
        if abs(height_for_width(cw) - ch) <= 1:
            return  # already 3:4 (WM_SIZING has done it, or our own resize)
        if self._normal_size is not None and (pos.cx, pos.cy) == self._client_of(self._normal_size):
            # After a restore WinForms re-applies the client size it stored as the window size of a borderless
            # form (it does not know about the frame this hook added): keep the previous window size.
            pos.cx, pos.cy = self._normal_size
            return
        fw, fh = fit_aspect_box(cw, ch)
        min_w, min_h = self._min_client_physical()
        if fw < min_w:
            fw, fh = min_w, min_h
        pos.cx, pos.cy = fw + self._nc[0], fh + self._nc[1]

    def _top_grip_hit(self, lparam: int) -> int:
        x = ctypes.c_short(lparam & 0xFFFF).value
        y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
        rect = wintypes.RECT()
        _user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
        if not (rect.top <= y < rect.top + self.top_grip):
            return 0
        corner = max(self.top_grip * 3, 16)
        if x < rect.left + corner:
            return HTTOPLEFT
        if x >= rect.right - corner:
            return HTTOPRIGHT
        return HTTOP

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_NCCALCSIZE and wparam:
                params = ctypes.cast(lparam, ctypes.POINTER(_NCCALCSIZE_PARAMS)).contents
                original_top = params.rgrc[0].top
                result = _user32.CallWindowProcW(self._prev_proc, hwnd, msg, wparam, lparam)
                params.rgrc[0].top = original_top  # no title-bar band: the page owns the top edge
                return result
            if msg == WM_NCHITTEST and self.top_grip:
                result = _user32.CallWindowProcW(self._prev_proc, hwnd, msg, wparam, lparam)
                if result == HTCLIENT:
                    hit = self._top_grip_hit(int(lparam))
                    if hit:
                        return hit
                return result
            if msg == WM_SIZING:
                self._on_sizing(wparam, lparam)
                return 1
            if msg == WM_WINDOWPOSCHANGING:
                self._on_pos_changing(lparam)
                return _user32.CallWindowProcW(self._prev_proc, hwnd, msg, wparam, lparam)
            if msg == WM_WINDOWPOSCHANGED:
                pos = ctypes.cast(lparam, ctypes.POINTER(_WINDOWPOS)).contents
                if not (pos.flags & SWP_NOSIZE) and pos.x > -32000 and not self._restoring \
                        and not _user32.IsIconic(hwnd) and pos.cx > self._nc[0] and pos.cy > self._nc[1]:
                    self._normal_size = (pos.cx, pos.cy)
                return _user32.CallWindowProcW(self._prev_proc, hwnd, msg, wparam, lparam)
            if msg == WM_SIZE:
                if int(wparam) == SIZE_MINIMIZED:
                    self._restoring = True
                    return _user32.CallWindowProcW(self._prev_proc, hwnd, msg, wparam, lparam)
                result = _user32.CallWindowProcW(self._prev_proc, hwnd, msg, wparam, lparam)
                self._restoring = False  # WinForms' own restore handling ran inside the call above
                return result
            if msg == WM_GETMINMAXINFO:
                result = _user32.CallWindowProcW(self._prev_proc, hwnd, msg, wparam, lparam)
                min_w, min_h = self._min_client_physical()
                if min_w and min_h:
                    mmi = ctypes.cast(lparam, ctypes.POINTER(_MINMAXINFO)).contents
                    mmi.ptMinTrackSize.x = min_w + self._nc[0]
                    mmi.ptMinTrackSize.y = min_h + self._nc[1]
                return result
            if msg == WM_SYSCOMMAND and (int(wparam) & 0xFFF0) == SC_MAXIMIZE:
                return 0  # no maximize: it would break the aspect (keyboard, system menu, snap)
            if msg == WM_NCLBUTTONDBLCLK:
                return 0  # a double-click on the resize border would maximize vertically
            if msg == WM_EXITSIZEMOVE:
                self._last_rect = None
            if msg == WM_NCDESTROY:
                result = _user32.CallWindowProcW(self._prev_proc, hwnd, msg, wparam, lparam)
                self.installed = False
                return result
        except Exception:  # noqa: BLE001 - never let a callback exception escape into the message loop
            log.debug("window procedure hook failed for message 0x%04x", msg, exc_info=True)
        return _user32.CallWindowProcW(self._prev_proc, hwnd, msg, wparam, lparam)
