"""Application entry point: single instance, logging, engine, bridge and the resizable frameless window.

    python -m media_forge.shell.main [--debug] [--fake] [--ui-dir PATH] [FILE_OR_FOLDER ...]

Given files are added to the list; while another instance runs they are handed to its window (shell/instance.py).

The frozen exe starts here through build/entry.py. `--fake` runs the stand-in engine of tests/shell_fake_engine.py
(source tree only) so the window can be opened without the core.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import socket
import sys
import threading
from pathlib import Path
from typing import Any, Optional

from media_forge import __version__
from media_forge.core.selfupdate import APPLY_FLAG, build_scheduler, build_updater, run_apply

WINDOW_TITLE = "Media Forge"
MUTEX_NAME = "Local\\MediaForge.SingleInstance"
BACKGROUND = "#04070f"
# Window = page viewport, logical pixels (ARCHITECTURE.md 7). It is resizable down to MIN_SIZE, the smallest size the
# layout is designed for; its size and position are kept in the setting `window_bounds`. Without stored bounds it
# opens at the first ladder size whose size times the Scale setting fits the monitor work area.
SIZE_LADDER = ((630, 840), (600, 800), (570, 760), (540, 720), (510, 680), (480, 640))
START_SIZE = SIZE_LADDER[0]
MIN_SIZE = SIZE_LADDER[-1]
HTTP_PORT_FIRST = 42101  # fixed port range keeps the page origin (and its localStorage) stable between launches
HTTP_PORT_TRIES = 40
LOG_NAME = "app.log"
MSG_NOT_HANDED_OVER = "The files could not be added: the open Media Forge window did not answer."
MSG_NO_MEDIA_GIVEN = "No media files in the given folder."

log = logging.getLogger("media_forge.shell.main")
_keep_alive: list[Any] = []  # ctypes callbacks and hooks that must outlive the window


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Options and the files to add (Open with, Send To, a file dropped on the exe); `files` are made absolute,
    since a second start hands them to a window that runs in another working folder."""
    parser = argparse.ArgumentParser(prog="MediaForge", description="Media Forge", allow_abbrev=False)
    parser.add_argument("--debug", action="store_true", help="enable WebView2 dev tools and verbose logging")
    parser.add_argument("--fake", action="store_true", help="use the stand-in engine (source tree only)")
    parser.add_argument("--ui-dir", help="serve the UI from this folder instead of the bundled one")
    parser.add_argument("files", nargs="*", help="media files or folders to add to the list")
    args = parser.parse_intermixed_args(_files_as_paths(sys.argv[1:] if argv is None else argv))
    args.files = [os.path.abspath(f) for f in args.files if f.strip()]
    return args


OPTIONS = ("--debug", "--fake", "--ui-dir", "-h", "--help")


def _files_as_paths(argv: list[str]) -> list[str]:
    """`argv` with every argument that starts with a dash and is no option of the app made an absolute path, so a
    file such as `-1.mp4` (Open with passes a relative name) is taken as a file instead of ending the start."""
    out: list[str] = []
    value_next = False
    for arg in argv:
        if not value_next and arg.startswith("-") and arg not in OPTIONS and arg != "--" \
                and not arg.startswith("--ui-dir="):
            arg = os.path.abspath(arg)
        value_next = arg == "--ui-dir"
        out.append(arg)
    return out


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def bundle_root() -> Path:
    """Folder that holds media_forge/ui and icon.ico in a frozen build (PyInstaller's extraction folder)."""
    return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))


def resolve_ui_dir(override: Optional[str] = None) -> Path:
    """The folder with index.html: --ui-dir, the extraction folder when frozen, else the source tree."""
    if override:
        return Path(override).resolve()
    if getattr(sys, "frozen", False):
        return bundle_root() / "media_forge" / "ui"
    return Path(__file__).resolve().parents[1] / "ui"


def resolve_icon() -> Optional[str]:
    if getattr(sys, "frozen", False):
        candidate = bundle_root() / "icon.ico"
    else:
        candidate = project_root() / "build" / "icon.ico"
    return str(candidate) if candidate.is_file() else None


def pick_http_port() -> int:
    """First free port from the fixed range; 0 lets pywebview fall back to its own choice."""
    for port in range(HTTP_PORT_FIRST, HTTP_PORT_FIRST + HTTP_PORT_TRIES):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    return 0


def setup_logging(log_dir: Path, debug: bool) -> None:
    """Rotating app.log (1 MB x 3) for the `media_forge` loggers (core and shell) and pywebview; the console too with
    --debug. Safe in a windowed exe (no stderr)."""
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s")
    handlers: list[logging.Handler] = []
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(
            log_dir / LOG_NAME, maxBytes=1_000_000, backupCount=3, encoding="utf-8", delay=True))
    except OSError:
        pass
    if debug and sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    for name in ("media_forge", "pywebview"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG if debug else (logging.INFO if name == "media_forge" else logging.WARNING))
        for handler in handlers:
            handler.setFormatter(fmt)
            logger.addHandler(handler)

    def _excepthook(exc_type, exc, tb):
        logging.getLogger("media_forge.crash").critical("unhandled exception", exc_info=(exc_type, exc, tb))

    def _thread_excepthook(args):
        logging.getLogger("media_forge.crash").critical(
            "unhandled exception in thread %s", getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook


class ShellPaths:
    """The few data paths the shell needs, taken from the core's AppPaths (the only module that builds them)."""

    def __init__(self, paths: Any) -> None:
        self.home = Path(paths.home)
        self.log_dir = Path(getattr(paths, "log_dir", None) or self.home / "logs")
        self.webview_dir = Path(getattr(paths, "webview_dir", None) or self.home / "webview")
        self.updates_dir = Path(getattr(paths, "updates_dir", None) or self.home / "updates")
        self.exe_path = getattr(paths, "exe_path", None)
        self._paths = paths

    def ensure(self) -> None:
        ensure = getattr(self._paths, "ensure", None)
        if callable(ensure):
            ensure()
        for folder in (self.home, self.log_dir, self.webview_dir, self.updates_dir):
            folder.mkdir(parents=True, exist_ok=True)


def load_fake_module():
    """tests/shell_fake_engine.py from the source tree (not bundled in the exe)."""
    root = str(project_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    from tests import shell_fake_engine

    return shell_fake_engine


def resolve_paths(fake: bool) -> ShellPaths:
    try:
        from media_forge.core.paths import AppPaths
    except ImportError:
        if not fake:
            raise
        return ShellPaths(load_fake_module().FakePaths())
    return ShellPaths(AppPaths())


def build_engine(fake: bool, emit) -> Any:
    if fake:
        return load_fake_module().FakeEngine(emit, __version__)
    from media_forge.core.engine import Engine  # imported late: the core loads numpy / OpenCV / Pillow

    return Engine(emit, __version__)


def main(argv: Optional[list[str]] = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == APPLY_FLAG:
        # The downloaded exe swaps the installed one and restarts it: no mutex, logging, engine or window.
        return int(run_apply(raw[1:]) or 0)
    args = parse_args(raw)

    from . import instance, winutil
    from .bridge import Bridge, EventPump

    mutex = winutil.acquire_single_instance(MUTEX_NAME)
    if mutex is None:
        handed_over = not args.files or instance.send_files(args.files)
        winutil.bring_window_to_front(WINDOW_TITLE)
        if not handed_over:
            winutil.message_box(WINDOW_TITLE, MSG_NOT_HANDED_OVER, error=True)
        return 0
    receiver = instance.FileReceiver()  # opened at once: a second start may come while this one is still loading
    receiver.start()

    try:
        paths = resolve_paths(args.fake)
        paths.ensure()
    except Exception as exc:  # noqa: BLE001 - a windowed exe has no console; show the reason
        winutil.message_box(WINDOW_TITLE, f"Could not create the data folder:\n{exc}", error=True)
        return 3
    setup_logging(paths.log_dir, args.debug)
    ui_dir = resolve_ui_dir(args.ui_dir)
    index = ui_dir / "index.html"
    log.info("Media Forge %s starting (fake=%s, debug=%s, ui=%s, data=%s)", __version__, args.fake, args.debug,
             ui_dir, paths.home)
    if not index.is_file():
        winutil.message_box(WINDOW_TITLE, f"UI file not found:\n{index}", error=True)
        return 2

    pump = EventPump()
    try:
        engine = build_engine(args.fake, pump.emit)
    except Exception as exc:  # noqa: BLE001
        log.exception("the engine could not be created")
        winutil.message_box(WINDOW_TITLE, f"Could not start the app engine:\n{exc}", error=True)
        return 4

    bridge = Bridge(engine, pump, __version__)
    try:
        engine.start()  # loads the settings, starts the workers and the tool discovery; events wait in the pump
    except Exception:  # noqa: BLE001
        log.exception("engine start failed")
    # Files of the command line and of later starts: queued now, shown when the page is ready (the pump keeps the
    # events until then).
    def add_given(files: list[str]) -> None:
        items = bridge._add_dropped(files, MSG_NO_MEDIA_GIVEN)
        log.info("%d item(s) added to the list", len(items))

    if args.files:
        log.info("adding %d path(s) from the command line", len(args.files))
        add_given(args.files)
    receiver.set_handler(add_given)
    try:
        updater = build_updater(paths, __version__, bridge._on_update_status, bridge._on_update_toast)
        bridge._attach_updater(updater, build_scheduler(updater))
    except Exception:  # noqa: BLE001 - the app works without updates
        log.exception("the updater could not be created")

    # Before any window exists (pywebview's own SetProcessDPIAware then fails harmlessly): a move to a monitor of
    # another DPI keeps the logical size and a sharp page (WindowHook, WM_DPICHANGED).
    if not winutil.set_per_monitor_dpi_awareness():
        log.info("per-monitor DPI awareness is not available")
    percent, bounds = window_settings(engine)
    import webview  # imported late: pulls in pythonnet and WinForms

    window = webview.create_window(
        WINDOW_TITLE,
        url=str(index),
        js_api=bridge,
        **initial_window_args(percent, bounds, winutil.monitors()),
        frameless=True,
        easy_drag=False,
        resizable=False,
        shadow=True,
        text_select=True,  # the UI decides where selection is allowed; pywebview's global user-select:none is off
        background_color=BACKGROUND,
    )
    bridge._attach_window(window)
    _wire_window_events(window, bridge, engine, receiver)

    try:
        webview.start(
            gui="edgechromium",
            debug=args.debug,
            private_mode=False,
            storage_path=str(paths.webview_dir),
            http_port=pick_http_port(),
            icon=resolve_icon(),
        )
    except Exception:  # noqa: BLE001 - a windowed exe has no console; show the reason
        log.exception("the window could not be started")
        winutil.message_box(WINDOW_TITLE, "The window could not be started. Check that the Microsoft Edge WebView2 "
                            "Runtime is installed. Details are in the log.", error=True)
    finally:
        receiver.stop()
        bridge._shutdown()
        log.info("Media Forge stopped")
        logging.shutdown()
        winutil.release_single_instance(mutex)
    # Non-daemon .NET / WebView2 helper threads can keep the process alive after the last window is gone.
    os._exit(0)


def _wire_window_events(window: Any, bridge: Any, engine: Any, receiver: Any = None) -> None:
    state: dict[str, Any] = {"drop_wired": False, "native_done": False, "geometry": None}

    def on_shown() -> None:
        if not state["native_done"]:
            state["native_done"] = True

            def setup() -> None:
                state["geometry"] = _setup_native_window(window, bridge)

            _on_ui_thread(window, setup)
            threading.Thread(target=bridge._start_updates, name="update-startup", daemon=True).start()

    def on_loaded() -> None:
        if state["drop_wired"]:
            return
        try:
            # pywebview adds `pywebviewFullPath` to the files of this event dict only because a Python-side listener
            # exists (BRIDGE_API.md 5); the page's own drop handler never sees the path.
            window.dom.document.events.drop += bridge._on_drop
            state["drop_wired"] = True
        except Exception:  # noqa: BLE001
            log.warning("drag and drop is not available", exc_info=True)

    def on_closing() -> None:
        if receiver is not None:
            receiver.stop()  # a later start now gets no acknowledgement and says so, instead of losing its files
        geometry = state["geometry"]
        if geometry is not None:
            geometry.save()  # a move outside a move loop (Win+Shift+Arrow, snapping) ends none: the last position
        bridge._shutdown()

    window.events.shown += on_shown
    window.events.loaded += on_loaded
    window.events.closing += on_closing


def _on_ui_thread(window: Any, fn) -> None:
    """Run `fn` on the WinForms UI thread (pywebview calls event handlers on worker threads)."""
    native = getattr(window, "native", None)
    try:
        if native is not None and native.InvokeRequired:
            from System import Action  # pythonnet

            native.Invoke(Action(fn))
            return
    except Exception:  # noqa: BLE001
        log.debug("UI-thread invoke failed; running directly", exc_info=True)
    fn()


def _post_to_ui(window: Any, fn) -> None:
    """Queue `fn` on the WinForms UI thread to run after the current message; at once when that fails."""
    def run() -> None:
        try:
            fn()
        except Exception:  # noqa: BLE001 - an exception must not reach the WinForms message loop
            log.exception("a queued window update failed")

    native = getattr(window, "native", None)
    try:
        if native is not None:
            from System import Action  # pythonnet

            native.BeginInvoke(Action(run))
            return
    except Exception:  # noqa: BLE001
        log.debug("UI-thread post failed; running directly", exc_info=True)
    run()


def _ui_result(window: Any, fn) -> Any:
    """`fn()` run on the UI thread (synchronously), its result returned; None when it did not run."""
    result: list[Any] = []
    _on_ui_thread(window, lambda: result.append(fn()))
    return result[0] if result else None


def window_settings(engine: Any) -> tuple[int, Optional[dict]]:
    """(Scale percent, stored window bounds or None) from the engine's settings."""
    try:
        settings = engine.get_settings() or {}
    except Exception:  # noqa: BLE001
        settings = {}
    try:
        percent = int(settings.get("ui_scale", 100))
    except (TypeError, ValueError):
        percent = 100
    bounds = settings.get("window_bounds")
    keys = ("x", "y", "width", "height")
    if not (isinstance(bounds, dict) and all(isinstance(bounds.get(k), int) for k in keys)):
        bounds = None
    return max(1, percent), bounds


def initial_window_args(percent: int, bounds: Optional[dict], monitors) -> dict[str, int]:
    """Size and position for `webview.create_window`: logical px that pywebview multiplies by the DPI scale of the
    new window, which opens on the primary monitor. The stored bounds (restored onto a connected monitor) when there
    are any, else the start size centred by WinForms. The shown window is placed exactly by `WindowGeometry.start`;
    these values only keep it from opening elsewhere first."""
    from . import winutil

    placed = winutil.restored_rect(bounds, monitors, percent / 100.0, MIN_SIZE) if bounds else None
    if placed is None:
        return {"width": START_SIZE[0], "height": START_SIZE[1]}
    (x, y, w, h), _dpi_scale = placed
    primary = next((m[1] for m in monitors if m[2]), monitors[0][1]) or 1.0
    return {"x": int(round(x / primary)), "y": int(round(y / primary)),
            "width": int(round(w / primary)), "height": int(round(h / primary))}


def _monitor_at(monitors, point: tuple[int, int]):
    """(work area, DPI scale) of the monitor whose work area holds `point` (physical px), or None."""
    x, y = point
    for work, dpi_scale, _primary in monitors:
        if work[0] <= x < work[2] and work[1] <= y < work[3]:
            return work, dpi_scale
    return None


class WindowGeometry:
    """Size, position and Scale of the window (ARCHITECTURE.md 7): the WebView2 zoom factor plus a client size of
    logical size x zoom x DPI (the page keeps its logical layout), the stored bounds, the Scale steps that fit.
    Runs on the UI thread, except `save`, which only reads the window."""

    def __init__(self, window: Any, hook: Any, bridge: Any, steps) -> None:
        self._window = window
        self._hook = hook
        self._bridge = bridge
        self._steps = tuple(steps)
        self.chosen = 100   # the Scale setting (`ui_scale`)
        self.percent = 100  # the step applied: the chosen one, or the largest step below it that fits the monitor

    def _apply(self, x: int, y: int, w: int, h: int) -> None:
        self._window.native.SetBounds(x, y, w, h)

    def fitted_step(self, dpi_scale: float, work: Optional[tuple[int, int, int, int]]) -> int:
        """The largest Scale step not above the chosen one whose minimum window fits `work` at `dpi_scale`."""
        from . import winutil

        fits = [s for s in winutil.fitting_scales(self._steps, MIN_SIZE, dpi_scale, work) if s <= self.chosen]
        return max(fits) if fits else self.chosen

    def start(self, percent: int, bounds: Optional[dict]) -> tuple[int, int, int, int]:
        """The saved Scale and bounds (or the start size, centred) applied before the page shows anything. A Scale
        step whose minimum window does not fit the monitor is applied as the largest step that fits; the setting
        keeps the chosen step."""
        from . import winutil

        hook = self._hook
        self.chosen = max(1, int(percent))
        monitors = winutil.monitors() if bounds else []
        placed = winutil.restored_rect(bounds, monitors, self.chosen / 100.0, MIN_SIZE) if bounds else None
        target = _monitor_at(monitors, placed[0][:2]) if placed is not None else None
        if target is not None:
            self.percent = self.fitted_step(target[1], target[0])
        else:
            self.percent = self.fitted_step(hook.scale(), hook.work_area())
        if self.percent != self.chosen:
            log.info("Scale %s%% does not fit this monitor; %s%% is applied", self.chosen, self.percent)
        zoom = self.percent / 100.0
        hook.zoom = zoom
        winutil.set_zoom_factor(self._window, zoom)
        rect = None
        if bounds:
            placed = winutil.restored_rect(bounds, monitors, zoom, MIN_SIZE)
            if placed is not None:
                rect, dpi_scale = placed
                if abs(dpi_scale - hook.scale()) > 1e-3:
                    # Onto a monitor of another DPI: move first (WM_DPICHANGED rescales the window there), then
                    # size it at that monitor's DPI.
                    current = hook.client_rect() or rect
                    hook.place(rect[:2] + current[2:], self._apply)
                    placed = winutil.restored_rect(bounds, winutil.monitors(), zoom, MIN_SIZE)
                    rect = placed[0] if placed is not None else rect
        if rect is None:
            work = hook.work_area()
            dpi_scale = hook.scale()
            work_logical = None
            if work is not None:
                work_logical = (int((work[2] - work[0]) / dpi_scale), int((work[3] - work[1]) / dpi_scale))
            size = winutil.pick_window_size(SIZE_LADDER, work_logical, zoom)
            rect = winutil.clamp_rect(winutil.centered_rect(winutil.scaled_size(size, zoom * dpi_scale), work),
                                      work, hook.min_client())
        hook.place(rect, self._apply)
        self.publish_scales()
        placed_rect = hook.client_rect() or rect
        log.info("window %s at %s%% (client x, y, w, h), dpi scale %.2f", placed_rect, self.percent, hook.scale())
        return placed_rect

    def set_scale(self, percent: int) -> None:
        """A new Scale step chosen: the window size times the ratio of the steps around the same centre, clamped
        into the work area, never below the minimum at the new step."""
        self.chosen = max(1, int(percent))
        if self._rescale(self.chosen):
            self.save()
        self.publish_scales()

    def on_display(self) -> None:
        """Another monitor, DPI or work area: the largest fitting step not above the chosen one is applied (the
        chosen step comes back once it fits again), and the fitting steps are published."""
        step = self.fitted_step(self._hook.scale(), self._hook.work_area())
        if step != self.percent:
            log.info("Scale %s%% applied as %s%% on this monitor", self.chosen, step)
            self._rescale(step)
        self.publish_scales()

    def _rescale(self, percent: int) -> bool:
        """Apply the step `percent`; False when the window is minimized or gone (only the zoom changed)."""
        from . import winutil

        hook = self._hook
        old_zoom, zoom = self.percent / 100.0, percent / 100.0
        self.percent = percent
        hook.zoom = zoom
        winutil.set_zoom_factor(self._window, zoom)
        current = hook.client_rect()
        if current is None or hook.is_minimized():
            return False
        rect = winutil.rescaled_rect(current, zoom / old_zoom, hook.work_area(), hook.min_client(zoom))
        hook.place(rect, self._apply)
        log.info("window %s at %s%%", hook.client_rect(), self.percent)
        return True

    def fitting_scales(self) -> list[int]:
        from . import winutil

        return winutil.fitting_scales(self._steps, MIN_SIZE, self._hook.scale(), self._hook.work_area())

    def publish_scales(self) -> None:
        self._bridge._on_window_scales(self.fitting_scales(), self.percent)

    def save(self) -> None:
        """Store the bounds (`window_bounds`) of the restored window."""
        from . import winutil

        hook = self._hook
        rect = hook.client_rect()
        if rect is None or hook.is_minimized() or rect[2] <= 0 or rect[3] <= 0:
            return
        self._bridge._save_window_bounds(winutil.bounds_of(rect, hook.zoom, hook.scale()))


def _setup_native_window(window: Any, bridge: Any) -> Optional[WindowGeometry]:
    """On the UI thread after the form is shown: the resize hook, the saved Scale and bounds, DWM corners and border
    colour, focus tracking. The zoom is set before the page shows anything (the page waits for the bridge)."""
    from media_forge.core.settings import SCALE_STEPS

    from . import winutil

    native = window.native
    if native is None:
        return None
    geometry = None
    try:
        hwnd = int(native.Handle.ToInt64())
        winutil.set_rounded_corners(hwnd)
        winutil.set_border_color(hwnd, BACKGROUND)
        hook = winutil.WindowHook(hwnd, MIN_SIZE)
        if hook.install():
            _keep_alive.append(hook)  # the ctypes callback must outlive the window
            geometry = WindowGeometry(window, hook, bridge, SCALE_STEPS)
            bridge._attach_scale(lambda value: _on_ui_thread(window, lambda: geometry.set_scale(value)))
            bridge._attach_drag(lambda part: _ui_result(window, lambda: hook.begin_drag(part)))
            geometry.start(*window_settings(bridge._engine))  # its `window` event carries `drag` and `scales`
            hook.on_size_end = geometry.save
            # after the message that reported the change: the refit resizes the window and sets the zoom
            hook.on_display = lambda: _post_to_ui(window, geometry.on_display)
        else:
            log.warning("window hook was not installed")
    except Exception:  # noqa: BLE001
        log.exception("DWM / window size setup failed")
    try:
        native.Activated += lambda *_: bridge._on_focus(True)
        native.Deactivate += lambda *_: bridge._on_focus(False)
    except Exception:  # noqa: BLE001
        log.exception("focus tracking unavailable")
    return geometry


if __name__ == "__main__":
    sys.exit(main())
