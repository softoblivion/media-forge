"""pywebview `js_api` object (`Bridge`), the Python -> JS event pump (`EventPump`) and the drop-path expansion.

Contract: docs/BRIDGE_API.md. pywebview exposes every public attribute of the js_api object, so `Bridge` has exactly
the methods of BRIDGE_API.md section 2 as public names; everything else starts with an underscore. Do not add public
helpers or public data attributes to it.
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from . import winutil

try:  # the core's user-facing error; the shell still runs with a stand-in engine when the core is absent
    from media_forge.core.errors import UserError
except ImportError:  # pragma: no cover - only without the core package
    class UserError(Exception):  # type: ignore[no-redef]
        """Fallback with the same meaning as media_forge.core.errors.UserError."""

log = logging.getLogger("media_forge.shell.bridge")
ui_log = logging.getLogger("media_forge.ui")

# --- media extensions (CORE.md 2; the same table as the open dialog filters) -------------------------------------

VIDEO_EXTENSIONS = (".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".flv", ".ts", ".m2ts", ".mts", ".mpg",
                    ".mpeg", ".3gp", ".gif")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".avif", ".heic", ".heif", ".tif", ".tiff", ".bmp", ".gif",
                    ".jxl")
AUDIO_EXTENSIONS = (".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma", ".aiff", ".aif")
MEDIA_EXTENSIONS = frozenset(VIDEO_EXTENSIONS + IMAGE_EXTENSIONS + AUDIO_EXTENSIONS)


def _patterns(extensions: Iterable[str]) -> str:
    seen: list[str] = []
    for ext in extensions:
        if ext not in seen:
            seen.append(ext)
    return ";".join("*" + ext for ext in seen)


FILE_TYPES = (
    f"Media files ({_patterns(VIDEO_EXTENSIONS + IMAGE_EXTENSIONS + AUDIO_EXTENSIONS)})",
    f"Video ({_patterns(VIDEO_EXTENSIONS)})",
    f"Images ({_patterns(IMAGE_EXTENSIONS)})",
    f"Audio ({_patterns(AUDIO_EXTENSIONS)})",
    "All files (*.*)",
)

CLEAR_KINDS = ("done", "errors", "idle", "all")
LOG_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}
MAX_UI_LOG_CHARS = 2000
_DIALOG_OPEN = 10    # webview.FileDialog.OPEN
_DIALOG_FOLDER = 20  # webview.FileDialog.FOLDER

# Sentences of BRIDGE_API.md 3.5 for refused settings patches.
MSG_OUTPUT_DIR = "The output folder does not exist."
MSG_SUFFIX = "A suffix is needed when files are written next to the source."
MSG_SETTING_REFUSED = "The setting could not be changed."
MSG_FAILED = "The operation failed; details are in the log."


class BridgeError(Exception):
    """An error whose message is already a user-facing English sentence."""


def _sentence(text: str) -> str:
    return " ".join(str(text).split())


def friendly_message(exc: BaseException) -> str:
    """One English sentence for the UI. Messages written by the bridge and the core pass through unchanged."""
    text = _sentence(getattr(exc, "message", None) or str(exc))
    if isinstance(exc, (BridgeError, UserError, ValueError)) and text:
        return text
    if isinstance(exc, PermissionError):
        return "No access to the file or folder."
    if isinstance(exc, FileNotFoundError):
        return "The file was not found."
    if isinstance(exc, OSError):
        return "File system error" + (f": {exc.strerror}." if exc.strerror else ".")
    return MSG_FAILED


def settings_refusal_message(exc: BaseException) -> str:
    """The BRIDGE_API.md 3.5 sentence for a settings patch the core refused: a sentence of the core passes through;
    a bare reason code (no sentence) naming the output folder or the suffix becomes the contract sentence."""
    text = _sentence(getattr(exc, "message", None) or str(exc))
    if text in (MSG_OUTPUT_DIR, MSG_SUFFIX):
        return text
    if isinstance(exc, (BridgeError, UserError, ValueError)) and text.endswith(".") and " " in text:
        return text
    low = text.lower()
    if "suffix" in low:
        return MSG_SUFFIX
    if "output" in low and ("dir" in low or "folder" in low):
        return MSG_OUTPUT_DIR
    if isinstance(exc, (BridgeError, UserError, ValueError)) and text:
        return text
    return MSG_SETTING_REFUSED


def _api(fn: Callable) -> Callable:
    """Wrap a Bridge method: log failures and re-raise them as BridgeError with an English sentence."""

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except BridgeError as exc:
            log.info("bridge.%s refused: %s", fn.__name__, exc)
            raise
        except Exception as exc:  # noqa: BLE001 - the UI needs a message for any failure
            if isinstance(exc, (UserError, ValueError)):
                log.info("bridge.%s rejected: %s", fn.__name__, exc)  # an expected, user-facing failure
            else:
                log.exception("bridge.%s failed", fn.__name__)
            raise BridgeError(friendly_message(exc)) from None

    # pywebview reads parameter names with inspect.getfullargspec, which honours __signature__ but not __wrapped__.
    wrapper.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
    return wrapper


# --- dropped paths -----------------------------------------------------------------------------------------------

def is_media_path(path: str | os.PathLike) -> bool:
    return Path(path).suffix.lower() in MEDIA_EXTENSIONS


def expand_paths(paths: Iterable[str]) -> list[str]:
    """Files as given; a folder becomes the media files directly inside it (one level, sorted by name, no
    recursion). Duplicates are dropped, the order is kept; missing paths are passed on (the engine reports them)."""
    out: list[str] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen:
            seen.add(key)
            out.append(path)

    for raw in paths:
        if not isinstance(raw, str) or not raw.strip():
            continue
        path = os.path.normpath(raw.strip())
        if os.path.isdir(path):
            try:
                entries = sorted(os.scandir(path), key=lambda e: e.name.lower())
            except OSError:
                log.info("cannot list the dropped folder %s", path, exc_info=True)
                continue
            for entry in entries:
                try:
                    if entry.is_file() and is_media_path(entry.name):
                        add(entry.path)
                except OSError:
                    continue
        else:
            add(path)
    return out


def dropped_paths(event: Any) -> list[str]:
    """`pywebviewFullPath` of every file in a pywebview DOM drop event (NOTES.md, "Dropped files have no path")."""
    files = ((event or {}).get("dataTransfer") or {}).get("files") or []
    paths = []
    for item in files:
        path = item.get("pywebviewFullPath") if isinstance(item, dict) else None
        if isinstance(path, str) and path:
            paths.append(path)
    return paths


# --- event pump --------------------------------------------------------------------------------------------------

def js_literal(value: Any) -> str:
    """JSON text that is also a safe JavaScript expression (U+2028 / U+2029 escaped, non-ASCII kept)."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def build_event_script(events: list[tuple[str, Any]]) -> str:
    """One JS snippet that calls window.mediaForge.dispatch for every event, isolating failures per event."""
    calls = "".join(
        f"try{{d({js_literal(name)},{js_literal(payload)})}}catch(e){{console.error(e)}};" for name, payload in events
    )
    return ("(function(){var h=window.mediaForge;if(!h||typeof h.dispatch!=='function')return;"
            "var d=function(n,p){h.dispatch(n,p)};" + calls + "})();")


COALESCED_EVENTS = frozenset({"item"})


def _coalesce_key(name: str, payload: Any) -> Optional[str]:
    if name in COALESCED_EVENTS and isinstance(payload, dict) and payload.get("id") is not None:
        return f"{name}:{payload['id']}"
    return None


class EventPump:
    """Thread-safe queue of events for the page.

    - Nothing is delivered before `set_ready()` (the page called ready()); everything emitted earlier is kept.
    - `item` events are coalesced per item id (key = name + id): a pending event keeps its place in the queue and
      takes the newest payload, so only the newest state of an item is delivered when the page is slower than the
      engine. Every other event is delivered in order.
    - `item_removed` drops a pending `item` event of that id (a removed item cannot reappear); `items_added` moves
      pending `item` events of its items behind itself (the page learns about the item before its updates).
    - Events are delivered in batches through `deliver(script)` (window.run_js) on one daemon thread, at most one
      batch per `min_interval` seconds.
    """

    def __init__(self, deliver: Optional[Callable[[str], Any]] = None, start_delay: float = 0.1,
                 min_interval: float = 0.04, threaded: bool = True) -> None:
        self._deliver = deliver
        self._start_delay = start_delay
        self._min_interval = min_interval
        self._threaded = threaded
        self._cond = threading.Condition()
        self._queue: list[tuple[str, Any, Optional[str]]] = []  # (name, payload, coalesce key)
        self._pending: dict[str, int] = {}  # coalesce key -> index in _queue
        self._ready_at: Optional[float] = None
        self._next_at = 0.0
        self._stopped = False
        self._thread: Optional[threading.Thread] = None

    # -- producer side
    def set_deliver(self, deliver: Callable[[str], Any]) -> None:
        with self._cond:
            self._deliver = deliver
            self._cond.notify_all()

    def emit(self, name: str, payload: Any) -> None:
        with self._cond:
            if self._stopped:
                return
            key = _coalesce_key(name, payload)
            if key is not None and key in self._pending:
                index = self._pending[key]
                self._queue[index] = (name, payload, key)
            else:
                if name == "item_removed" and isinstance(payload, dict):
                    self._drop(f"item:{payload.get('id')}")
                    self._append(name, payload, None)
                elif name == "items_added" and isinstance(payload, dict):
                    moved = []
                    for item in payload.get("items") or []:
                        if isinstance(item, dict):
                            moved_entry = self._drop(f"item:{item.get('id')}")
                            if moved_entry is not None:
                                moved.append(moved_entry)
                    self._append(name, payload, None)
                    for entry in moved:
                        self._append(*entry)
                else:
                    self._append(name, payload, key)
            self._cond.notify_all()

    def _append(self, name: str, payload: Any, key: Optional[str]) -> None:
        self._queue.append((name, payload, key))
        if key is not None:
            self._pending[key] = len(self._queue) - 1

    def _drop(self, key: str) -> Optional[tuple[str, Any, Optional[str]]]:
        index = self._pending.pop(key, None)
        if index is None:
            return None
        entry = self._queue.pop(index)
        self._pending = {k: (i - 1 if i > index else i) for k, i in self._pending.items()}
        return entry

    def set_ready(self) -> None:
        """The page called ready(): start delivering, after a short grace so the return value of ready() goes first."""
        with self._cond:
            if self._ready_at is None:
                self._ready_at = time.monotonic() + self._start_delay
            self._cond.notify_all()
            start = self._threaded and self._thread is None and not self._stopped
            if start:
                self._thread = threading.Thread(target=self._run, name="event-pump", daemon=True)
        if start:
            self._thread.start()  # type: ignore[union-attr]

    def stop(self) -> None:
        with self._cond:
            self._stopped = True
            self._cond.notify_all()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)

    @property
    def ready(self) -> bool:
        return self._ready_at is not None

    def pending(self) -> list[tuple[str, Any]]:
        """A copy of the queued events (tests and diagnostics)."""
        with self._cond:
            return [(name, payload) for name, payload, _ in self._queue]

    # -- consumer side
    def take(self) -> list[tuple[str, Any]]:
        """Remove and return every queued event when delivery is allowed, else []."""
        with self._cond:
            if self._ready_at is None or self._deliver is None or not self._queue:
                return []
            events = [(name, payload) for name, payload, _ in self._queue]
            self._queue = []
            self._pending = {}
            return events

    def flush(self) -> int:
        """Deliver everything queued now; returns the number of events delivered (the thread and tests call it)."""
        events = self.take()
        if not events:
            return 0
        deliver = self._deliver
        try:
            deliver(build_event_script(events))  # type: ignore[misc]
        except Exception:  # noqa: BLE001 - the window may be closing; events are not worth crashing for
            log.debug("event delivery failed", exc_info=True)
        return len(events)

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._stopped:
                    if self._ready_at is not None and self._deliver is not None and self._queue:
                        delay = max(self._ready_at, self._next_at) - time.monotonic()
                        if delay <= 0:
                            break
                        self._cond.wait(delay)
                    else:
                        self._cond.wait()
                if self._stopped:
                    return
            self.flush()
            self._next_at = time.monotonic() + self._min_interval


# --- bridge ------------------------------------------------------------------------------------------------------

def _default_update_status(version: str) -> dict:
    """UpdateStatus while no updater is attached (BRIDGE_API.md 3.8)."""
    return {"stage": "idle", "current": version, "latest": None, "notes": None, "size": None, "published_at": None,
            "progress": None, "message": "Updates are not checked yet", "checked_at": None, "can_apply": False}


class Bridge:
    """The js_api object. Contract: docs/BRIDGE_API.md, section 2 (methods) and 6 (shell rules)."""

    def __init__(self, engine: Any, pump: EventPump, version: str,
                 reveal: Optional[Callable[[str], bool]] = None,
                 opener: Optional[Callable[[str], bool]] = None,
                 url_opener: Optional[Callable[[str], bool]] = None) -> None:
        self._engine = engine
        self._pump = pump
        self._version = version
        self._reveal = reveal or winutil.reveal_in_explorer
        self._opener = opener or winutil.open_path
        self._url_opener = url_opener or winutil.open_url
        self._window: Any = None
        self._updater: Any = None
        self._scheduler: Any = None
        self._lock = threading.RLock()
        self._focused = True
        self._last_dir = ""
        self._auto_download_for: Optional[str] = None
        self._shut_down = False

    # -- wiring (underscore names are not exposed to JS)
    def _attach_window(self, window: Any) -> None:
        self._window = window
        deliver = getattr(window, "run_js", None) or window.evaluate_js
        self._pump.set_deliver(deliver)

    def _attach_updater(self, updater: Any, scheduler: Any = None) -> None:
        """The updater and its periodic scheduler; the scheduler starts after the launch check."""
        self._updater = updater
        self._scheduler = scheduler

    def _emit(self, name: str, payload: Any) -> None:
        if name == "batch_done":
            self._after_batch(payload)
        self._pump.emit(name, payload)

    def _after_batch(self, payload: Any) -> None:
        """`open_when_done` (BRIDGE_API.md 3.5): reveal the last finished output of the batch in Explorer."""
        try:
            if not (isinstance(payload, dict) and payload.get("done")) or not self._setting("open_when_done", False):
                return
            items = (self._engine.state() or {}).get("items") or []
            outputs = [i.get("output", {}).get("path") for i in items if i.get("status") == "done" and i.get("output")]
            if outputs:
                self._reveal(outputs[-1])
        except Exception:  # noqa: BLE001 - a convenience must never break the event flow
            log.debug("open_when_done failed", exc_info=True)

    def _toast(self, message: str, kind: str = "info") -> None:
        self._emit("toast", {"kind": kind, "message": message})

    def _setting(self, key: str, default: Any = None) -> Any:
        try:
            return (self._engine.get_settings() or {}).get(key, default)
        except Exception:  # noqa: BLE001
            log.debug("could not read the settings", exc_info=True)
            return default

    # -- window state
    def _on_focus(self, focused: bool) -> None:
        focused = bool(focused)
        with self._lock:
            if focused == self._focused:
                return
            self._focused = focused
        self._emit("window", {"focused": focused})

    # -- drop listener (main registers it on window.dom.document.events.drop)
    def _on_drop(self, event: Any) -> None:
        paths = dropped_paths(event)
        if not paths:
            return
        self._add_dropped(paths)

    def _add_dropped(self, paths: list[str]) -> list:
        expanded = expand_paths(paths)
        if not expanded:
            self._toast("No media files in the dropped folder.")
            return []
        try:
            items = list(self._engine.add_files(expanded) or [])
        except Exception as exc:  # noqa: BLE001 - a drop has no promise to reject: tell the page with a toast
            if not isinstance(exc, (UserError, ValueError)):
                log.exception("adding dropped files failed")
            self._toast(friendly_message(exc), "error")
            return []
        if items:
            self._emit("items_added", {"items": items})
        return items

    # -- state composition
    def _update_status(self) -> dict:
        if self._updater is None:
            return _default_update_status(self._version)
        return self._updater.status()

    def _state(self) -> dict:
        state = dict(self._engine.state() or {})
        state["version"] = self._version
        state["update"] = self._update_status()
        state["window"] = {"focused": self._focused}
        return state

    # -- dialogs
    def _need_window(self) -> Any:
        if self._window is None:
            raise BridgeError("The window is not ready.")
        return self._window

    def _dialog_kind(self, name: str, fallback: int) -> Any:
        try:
            import webview

            return getattr(webview.FileDialog, name)
        except Exception:  # noqa: BLE001 - no pywebview (tests): the fake window ignores the value
            return fallback

    # -- self-update (wiring called by main / the updater thread)
    def _on_update_status(self, status: dict) -> None:
        self._emit("update", status)
        if status.get("stage") != "available" or not self._setting("auto_download_updates", False):
            return
        version = str(status.get("latest") or "")
        with self._lock:
            if self._auto_download_for == version:
                return
            self._auto_download_for = version
        # The updater is still inside its check step here, so a download started now would be refused: retry briefly.
        threading.Thread(target=self._auto_download, name="update-auto-download", daemon=True).start()

    def _auto_download(self) -> None:
        updater = self._updater
        if updater is None:
            return
        for _ in range(50):
            if updater.status().get("stage") != "available":
                return
            updater.download_background()
            if updater.status().get("stage") == "downloading":
                return
            time.sleep(0.1)

    def _on_update_toast(self, level: str, text: str) -> None:
        self._toast(text, "success" if level == "success" else ("error" if level == "error" else "info"))

    def _start_updates(self) -> None:
        """Launch check: clean-up of a finished update, a background check, then the periodic schedule."""
        updater = self._updater
        if updater is None:
            return
        try:
            updater.startup()
            updater.check_background()
        except Exception:  # noqa: BLE001
            log.warning("update check could not start", exc_info=True)
        if self._scheduler is not None:
            try:
                self._scheduler.start()
                log.info("periodic update checks every %s", getattr(self._scheduler, "interval_key", "?"))
            except Exception:  # noqa: BLE001
                log.warning("periodic update checks could not start", exc_info=True)

    # -- shutdown
    def _close_window(self) -> None:
        window = self._window
        if window is not None:
            try:
                window.destroy()
            except Exception:  # noqa: BLE001
                log.debug("window destroy failed", exc_info=True)

    def _close_soon(self, delay: float = 0.2) -> None:
        """Close the window right after the current call has returned its value to the page."""
        timer = threading.Timer(delay, self._close_window)
        timer.daemon = True
        timer.start()

    def _shutdown(self) -> None:
        """Idempotent: stop the scheduler, cancel every job and stop the engine (it saves its settings), stop the
        pump (BRIDGE_API.md 6)."""
        with self._lock:
            if self._shut_down:
                return
            self._shut_down = True
        if self._scheduler is not None:
            try:
                self._scheduler.stop()
            except Exception:  # noqa: BLE001
                log.debug("scheduler stop failed", exc_info=True)
        try:
            self._engine.cancel_all()
        except Exception:  # noqa: BLE001
            log.warning("cancel_all at shutdown failed", exc_info=True)
        try:
            self._engine.stop()
        except Exception:  # noqa: BLE001
            log.warning("engine stop failed", exc_info=True)
        save = getattr(self._engine, "save_settings", None)
        if callable(save):
            try:
                save()
            except Exception:  # noqa: BLE001
                log.warning("settings save failed", exc_info=True)
        self._pump.stop()

    # ================================================================================================================
    # Contract methods (BRIDGE_API.md 2). Every public name below is exposed to the page.

    @_api
    def ready(self):
        state = self._state()
        self._pump.set_ready()
        return state

    @_api
    def add_files(self, paths):
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise BridgeError("Invalid file list.")
        expanded = expand_paths(paths)
        if not expanded:
            return []
        return list(self._engine.add_files(expanded) or [])

    @_api
    def pick_files(self):
        window = self._need_window()
        directory = self._last_dir if self._last_dir and os.path.isdir(self._last_dir) else ""
        result = window.create_file_dialog(self._dialog_kind("OPEN", _DIALOG_OPEN), directory=directory,
                                           allow_multiple=True, file_types=FILE_TYPES)
        if not result:
            return []
        paths = [str(p) for p in (result if isinstance(result, (list, tuple)) else [result])]
        self._last_dir = os.path.dirname(paths[0])
        return list(self._engine.add_files(paths) or [])

    @_api
    def remove_item(self, id):  # noqa: A002 - parameter name is part of the JS contract
        _need_id(id)
        result = self._engine.remove_item(id)
        return bool(result) if isinstance(result, bool) else True

    @_api
    def clear(self, what):
        if what not in CLEAR_KINDS:
            raise BridgeError("Unknown list filter.")
        return list(self._engine.clear(what) or [])

    @_api
    def set_item_settings(self, id, patch):  # noqa: A002
        _need_id(id)
        if not isinstance(patch, dict):
            raise BridgeError("Invalid settings.")
        return self._engine.set_item_settings(id, patch)

    @_api
    def apply_to_kind(self, id):  # noqa: A002
        _need_id(id)
        return list(self._engine.apply_to_kind(id) or [])

    @_api
    def start(self, ids=None):
        if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)):
            raise BridgeError("Invalid file list.")
        return list(self._engine.start_items(ids) or [])

    @_api
    def start_now(self, id):  # noqa: A002
        _need_id(id)
        return bool(self._engine.start_now(id))

    @_api
    def cancel(self, id):  # noqa: A002
        _need_id(id)
        return bool(self._engine.cancel(id))

    @_api
    def cancel_all(self):
        return int(self._engine.cancel_all() or 0)

    @_api
    def get_settings(self):
        return self._engine.get_settings()

    @_api
    def set_settings(self, patch):
        if not isinstance(patch, dict):
            raise BridgeError("Invalid settings.")
        before = self._engine.get_settings() or {}
        try:
            after = self._engine.set_settings(patch)
        except Exception as exc:  # noqa: BLE001 - the core's Settings validates; refusals become 3.5 sentences
            message = settings_refusal_message(exc)
            if not isinstance(exc, (BridgeError, UserError, ValueError)):
                log.exception("set_settings failed")
            try:
                self._emit("settings", self._engine.get_settings())  # the page restores the old values
            except Exception:  # noqa: BLE001
                log.debug("could not re-send the settings", exc_info=True)
            raise BridgeError(message) from None
        after = after if isinstance(after, dict) else (self._engine.get_settings() or {})
        interval = after.get("update_check_interval")
        if interval != before.get("update_check_interval") and self._scheduler is not None and interval:
            self._scheduler.set_interval(interval)
            log.info("periodic update checks every %s", interval)
        if after.get("auto_download_updates") and not before.get("auto_download_updates") \
                and self._updater is not None:
            status = self._updater.status()
            if status.get("stage") == "available":
                self._on_update_status(status)
        return after

    @_api
    def pick_output_dir(self):
        window = self._need_window()
        current = str(self._setting("output_dir", "") or "")
        result = window.create_file_dialog(self._dialog_kind("FOLDER", _DIALOG_FOLDER),
                                           directory=current if current and os.path.isdir(current) else "")
        if not result:
            return None
        return str(result[0] if isinstance(result, (list, tuple)) else result)

    @_api
    def reveal(self, path):
        if not isinstance(path, str) or not path.strip():
            return False
        return bool(self._reveal(path))

    @_api
    def open_path(self, path):
        if not isinstance(path, str) or not path.strip():
            return False
        return bool(self._opener(path))

    @_api
    def open_url(self, url):
        if not isinstance(url, str) or not url.strip():
            return False
        return bool(self._url_opener(url))

    @_api
    def capabilities(self):
        return self._engine.capabilities()

    @_api
    def window_minimize(self):
        self._need_window().minimize()
        return None

    @_api
    def window_close(self):
        self._need_window()
        self._close_soon()
        return None

    @_api
    def update_status(self):
        return self._update_status()

    @_api
    def check_updates(self):
        updater = self._need_updater()
        updater.check_background()
        return updater.status()

    @_api
    def download_update(self):
        updater = self._need_updater()
        updater.download_background()
        return updater.status()

    @_api
    def apply_update(self):
        if not self._need_updater().apply():
            return False
        # The apply-mode exe waits for this process to exit: close the window right after this call has returned.
        self._close_soon(0.3)
        return True

    @_api
    def log(self, level, message):
        text = str(message)[:MAX_UI_LOG_CHARS]
        ui_log.log(LOG_LEVELS.get(str(level).lower(), logging.INFO), "ui: %s", text)
        return None

    # ================================================================================================================

    def _need_updater(self) -> Any:
        if self._updater is None:
            raise BridgeError("Updates are not available.")
        return self._updater


def _need_id(value: Any) -> None:
    if not isinstance(value, str) or not value:
        raise BridgeError("The file is no longer in the list.")


# The public names of Bridge must be exactly the contract methods (checked by tests/test_shell_bridge.py).
CONTRACT_METHODS = (
    "ready", "add_files", "pick_files", "remove_item", "clear", "set_item_settings", "apply_to_kind", "start",
    "start_now", "cancel", "cancel_all", "get_settings", "set_settings", "pick_output_dir", "reveal", "open_path",
    "open_url", "capabilities", "window_minimize", "window_close", "update_status", "check_updates",
    "download_update", "apply_update", "log",
)
