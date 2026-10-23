"""Application entry point: single instance, logging, engine, bridge and the fixed-size frameless 3:4 window.

    python -m media_forge.shell.main [--debug] [--fake] [--ui-dir PATH]

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
# Window = page viewport, logical pixels, 3:4, not resizable (ARCHITECTURE.md 7). The first size whose size times
# the Scale setting fits the monitor work area is used; the two smallest steps exist for the larger scales.
SIZE_LADDER = ((630, 840), (600, 800), (570, 760), (540, 720), (510, 680), (480, 640))
START_SIZE = SIZE_LADDER[0]
HTTP_PORT_FIRST = 42101  # fixed port range keeps the page origin (and its localStorage) stable between launches
HTTP_PORT_TRIES = 40
LOG_NAME = "app.log"

log = logging.getLogger("media_forge.shell.main")
_keep_alive: list[Any] = []  # ctypes callbacks and hooks that must outlive the window


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="MediaForge", description="Media Forge")
    parser.add_argument("--debug", action="store_true", help="enable WebView2 dev tools and verbose logging")
    parser.add_argument("--fake", action="store_true", help="use the stand-in engine (source tree only)")
    parser.add_argument("--ui-dir", help="serve the UI from this folder instead of the bundled one")
    return parser.parse_args(argv)


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

    from . import winutil
    from .bridge import Bridge, EventPump

    mutex = winutil.acquire_single_instance(MUTEX_NAME)
    if mutex is None:
        winutil.bring_window_to_front(WINDOW_TITLE)
        return 0

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
    try:
        updater = build_updater(paths, __version__, bridge._on_update_status, bridge._on_update_toast)
        bridge._attach_updater(updater, build_scheduler(updater))
    except Exception:  # noqa: BLE001 - the app works without updates
        log.exception("the updater could not be created")

    import webview  # imported late: pulls in pythonnet and WinForms

    window = webview.create_window(
        WINDOW_TITLE,
        url=str(index),
        js_api=bridge,
        width=START_SIZE[0],
        height=START_SIZE[1],
        frameless=True,
        easy_drag=False,
        resizable=False,
        shadow=True,
        text_select=True,  # the UI decides where selection is allowed; pywebview's global user-select:none is off
        background_color=BACKGROUND,
    )
    bridge._attach_window(window)
    _wire_window_events(window, bridge, engine)

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
        bridge._shutdown()
        log.info("Media Forge stopped")
        logging.shutdown()
        winutil.release_single_instance(mutex)
    # Non-daemon .NET / WebView2 helper threads can keep the process alive after the last window is gone.
    os._exit(0)


def _wire_window_events(window: Any, bridge: Any, engine: Any) -> None:
    state = {"drop_wired": False, "native_done": False}

    def on_shown() -> None:
        if not state["native_done"]:
            state["native_done"] = True
            _on_ui_thread(window, lambda: _setup_native_window(window, bridge))
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


class WindowScale:
    """The Scale setting applied to the window: the WebView2 zoom factor plus the locked window size (logical
    size times the factor; the page keeps its logical layout). `apply` runs on the UI thread."""

    def __init__(self, window: Any, hook: Any, ladder) -> None:
        self._window = window
        self._hook = hook
        self._ladder = ladder
        self.percent = 100

    def apply(self, percent: int, keep_position: bool = False) -> None:
        from . import winutil

        zoom = max(1, int(percent)) / 100.0
        self.percent = int(percent)
        winutil.set_zoom_factor(self._window, zoom)
        width, height = self._hook.fit_size(self._ladder, zoom)
        rect = self._hook.lock(width, height, apply=self._window.native.SetBounds, zoom=zoom,
                               keep_position=keep_position)
        log.info("window %sx%s logical at %s%%, %sx%s physical, dpi scale %.2f", width, height, self.percent,
                 rect[2], rect[3], winutil.window_scale(self._hook.hwnd))


def _setup_native_window(window: Any, bridge: Any) -> None:
    """On the UI thread after the form is shown: fixed size fitted to the work area at the saved Scale, no
    maximize, DWM corners and border colour, focus tracking. The zoom is set before the page shows anything (the
    page waits for the bridge)."""
    from . import winutil

    native = window.native
    if native is None:
        return
    try:
        hwnd = int(native.Handle.ToInt64())
        winutil.set_rounded_corners(hwnd)
        winutil.set_border_color(hwnd, BACKGROUND)
        hook = winutil.FixedSizeHook(hwnd)
        if hook.install():
            _keep_alive.append(hook)  # the ctypes callback must outlive the window
            scale = WindowScale(window, hook, SIZE_LADDER)
            try:
                percent = int((bridge._engine.get_settings() or {}).get("ui_scale", 100))
            except Exception:  # noqa: BLE001
                percent = 100
            scale.apply(percent)
            bridge._attach_scale(lambda value: _on_ui_thread(window, lambda: scale.apply(value, keep_position=True)))
        else:
            log.warning("fixed-size hook was not installed")
    except Exception:  # noqa: BLE001
        log.exception("DWM / window size setup failed")
    try:
        native.Activated += lambda *_: bridge._on_focus(True)
        native.Deactivate += lambda *_: bridge._on_focus(False)
    except Exception:  # noqa: BLE001
        log.exception("focus tracking unavailable")


if __name__ == "__main__":
    sys.exit(main())
