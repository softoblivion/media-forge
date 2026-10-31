"""Stand-in for `media_forge.core.engine.Engine` (docs/ARCHITECTURE.md 6), used by the shell tests and by
`python -m media_forge.shell.main --fake`.

It keeps items in memory, "probes" by extension, simulates progress on a timer thread and validates the two
cross-key rules of the global settings the way the core's Settings does (BRIDGE_API.md 3.5). Nothing is read from
or written to the files it is given.
"""

from __future__ import annotations

import copy
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

VIDEO = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".flv", ".ts", ".m2ts", ".mts", ".mpg", ".mpeg",
         ".3gp"}
IMAGE = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".heic", ".heif", ".tif", ".tiff", ".bmp", ".gif", ".jxl"}
AUDIO = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma", ".aiff", ".aif"}

DEFAULTS = {
    "video": {"format": "mp4", "codec": "h264", "encoder": "auto", "quality": 70, "size_limit_mb": None,
              "scale": "none", "upscale_method": "lanczos", "sharpen": True, "fps": "keep", "audio": "keep",
              "audio_bitrate_k": 192, "strip_metadata": True},
    "image": {"format": "jpg", "quality": 85, "size_limit_mb": None, "scale": "none", "upscale_method": "lanczos",
              "sharpen": True, "strip_metadata": True},
    "audio": {"format": "mp3", "bitrate_k": 192, "size_limit_mb": None, "sample_rate": "keep", "channels": "keep",
              "normalize": False, "strip_metadata": True},
}

SETTINGS = {
    "output_mode": "beside", "output_dir": "", "suffix": "_forge", "parallel_jobs": 1, "open_when_done": False,
    "sound_when_done": True, "auto_download_updates": True, "skipped_major": None, "theme": "nova", "ui_scale": 100,
}

MSG_BUSY = "The file is being processed."
MSG_UNKNOWN = "The file is no longer in the list."
MSG_OUTPUT_DIR = "The output folder does not exist."
MSG_SUFFIX = "A suffix is needed when files are written next to the source."


class FakeUserError(ValueError):
    """Refusal with a user-facing sentence (the core raises UserError; the bridge treats both alike)."""


class FakePaths:
    """Data folder of a --fake run without the core: MEDIA_FORGE_HOME, else a temp folder (never the project)."""

    def __init__(self) -> None:
        env = os.environ.get("MEDIA_FORGE_HOME", "").strip()
        self.home = Path(env) if env else Path(tempfile.gettempdir()) / "media_forge_fake_home"
        self.log_dir = self.home / "logs"
        self.webview_dir = self.home / "webview"
        self.updates_dir = self.home / "updates"
        self.exe_path = Path(sys.executable) if getattr(sys, "frozen", False) else None

    def ensure(self) -> None:
        for folder in (self.home, self.log_dir, self.webview_dir, self.updates_dir):
            folder.mkdir(parents=True, exist_ok=True)


def kind_of(path: str) -> Optional[str]:
    ext = Path(path).suffix.lower()
    if ext in VIDEO:
        return "video"
    if ext in IMAGE:
        return "image"
    if ext in AUDIO:
        return "audio"
    return None


class FakeEngine:
    """Engine(emit, version) with the facade of ARCHITECTURE.md 6.

    `probe_delay` / `tick` control the simulated work (None probe delay = probe synchronously, for tests);
    `calls` records every facade call as (name, args) for the delegation tests.
    """

    def __init__(self, emit: Callable[[str, Any], None], version: str, probe_delay: Optional[float] = 0.3,
                 tick: float = 0.1, step: float = 0.05) -> None:
        self._emit = emit
        self.version = version
        self.probe_delay = probe_delay
        self.tick = tick
        self.step = step
        self.calls: list[tuple[str, tuple]] = []
        self._lock = threading.RLock()
        self._items: dict[str, dict] = {}
        self._order: list[str] = []
        self._next = 1
        self._settings = copy.deepcopy(SETTINGS)
        self._settings["defaults"] = copy.deepcopy(DEFAULTS)
        self._running = False
        self._stopped = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self.started = False
        self.stopped = False
        self._caps = {"ffmpeg": "ready", "ffmpeg_version": "fake", "ffmpeg_path": None,
                      "nvenc": {"h264": False, "h265": False, "av1": False}, "gpu_name": None, "avif": True,
                      "heif": False, "models": {"fsrcnn": "ready", "edsr": "missing"}}

    # -- helpers
    def _record(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))

    def _item(self, item_id: str) -> dict:
        item = self._items.get(item_id)
        if item is None:
            raise FakeUserError(MSG_UNKNOWN)
        return item

    def _send(self, item: dict) -> None:
        self._emit("item", copy.deepcopy(item))

    # -- facade
    def start(self) -> None:
        self._record("start")
        self.started = True
        self._emit("capabilities", copy.deepcopy(self._caps))
        self._worker = threading.Thread(target=self._run, name="fake-engine", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._record("stop")
        self.stopped = True
        self._stopped.set()
        if self._worker is not None:
            self._worker.join(timeout=2)

    def state(self) -> dict:
        self._record("state")
        with self._lock:
            return {"version": self.version, "settings": self.get_settings(),
                    "items": [copy.deepcopy(self._items[i]) for i in self._order],
                    "capabilities": copy.deepcopy(self._caps), "options": self.options()}

    def add_files(self, paths: list[str]) -> list[dict]:
        self._record("add_files", list(paths))
        added = []
        with self._lock:
            known = {os.path.normcase(i["path"]) for i in self._items.values()}
            skipped = 0
            for path in paths:
                if os.path.normcase(path) in known:
                    skipped += 1
                    continue
                known.add(os.path.normcase(path))
                item_id = f"i{self._next}"
                self._next += 1
                kind = kind_of(path) or "video"
                item = {"id": item_id, "path": path, "name": Path(path).name, "kind": kind, "size": None,
                        "status": "probing", "probe": None, "thumb": None,
                        "settings": copy.deepcopy(self._settings["defaults"][kind]), "progress": 0.0,
                        "stage": None, "eta": None, "speed": None, "output": None, "error": None,
                        "added_at": time.time()}
                self._items[item_id] = item
                self._order.append(item_id)
                added.append(copy.deepcopy(item))
        if skipped:
            self._emit("toast", {"kind": "info", "message": "Already in the list"})
        for item in added:
            if self.probe_delay is None:
                self._finish_probe(item["id"])
            else:
                timer = threading.Timer(self.probe_delay, self._finish_probe, args=(item["id"],))
                timer.daemon = True
                timer.start()
        return added

    def _finish_probe(self, item_id: str) -> None:
        with self._lock:
            item = self._items.get(item_id)
            if item is None or item["status"] != "probing":
                return
            if kind_of(item["path"]) is None:
                item.update(status="error", error="Not a media file.")
            else:
                item.update(status="ready", size=1_000_000, probe={
                    "duration": 10.0 if item["kind"] != "image" else None,
                    "width": 1920 if item["kind"] != "audio" else None,
                    "height": 1080 if item["kind"] != "audio" else None,
                    "fps": 30.0 if item["kind"] == "video" else None, "vcodec": None, "acodec": None,
                    "bitrate": None, "sample_rate": None, "channels": None,
                    "has_audio": item["kind"] != "image", "format": "fake"})
            snapshot = copy.deepcopy(item)
        self._emit("item", snapshot)

    def remove_item(self, item_id: str) -> bool:
        self._record("remove_item", item_id)
        with self._lock:
            self._item(item_id)
            del self._items[item_id]
            self._order.remove(item_id)
        self._emit("item_removed", {"id": item_id})
        return True

    def clear(self, what: str) -> list[str]:
        self._record("clear", what)
        with self._lock:
            def match(item: dict) -> bool:
                if what == "done":
                    return item["status"] == "done"
                if what == "errors":
                    return item["status"] == "error"
                if what == "idle":
                    return item["status"] not in ("queued", "running")
                return True
            ids = [i for i in self._order if match(self._items[i])]
            for item_id in ids:
                del self._items[item_id]
                self._order.remove(item_id)
        for item_id in ids:
            self._emit("item_removed", {"id": item_id})
        return ids

    def set_item_settings(self, item_id: str, patch: dict) -> dict:
        self._record("set_item_settings", item_id, dict(patch))
        with self._lock:
            item = self._item(item_id)
            if item["status"] in ("queued", "running"):
                raise FakeUserError(MSG_BUSY)
            allowed = DEFAULTS[item["kind"]]
            item["settings"].update({k: v for k, v in patch.items() if k in allowed})
            snapshot = copy.deepcopy(item)
        self._emit("item", snapshot)
        return snapshot

    def apply_to_kind(self, item_id: str) -> list[dict]:
        self._record("apply_to_kind", item_id)
        changed = []
        with self._lock:
            source = self._item(item_id)
            for other in self._items.values():
                if other is not source and other["kind"] == source["kind"] \
                        and other["status"] not in ("queued", "running"):
                    other["settings"] = copy.deepcopy(source["settings"])
                    changed.append(copy.deepcopy(other))
        for item in changed:
            self._emit("item", item)
        return changed

    def start_items(self, ids: Optional[list[str]]) -> list[str]:
        self._record("start_items", ids)
        with self._lock:
            chosen = self._order if ids is None else [i for i in self._order if i in set(ids)]
            queued = [i for i in chosen if self._items[i]["status"] == "ready"]
            for item_id in queued:
                self._items[item_id].update(status="queued", progress=0.0)
            snapshots = [copy.deepcopy(self._items[i]) for i in queued]
            if queued:
                self._running = True
        for item in snapshots:
            self._emit("item", item)
        return queued

    def start_now(self, item_id: str) -> bool:
        """Runs the item at once beside the others (`advance` moves every running item)."""
        self._record("start_now", item_id)
        with self._lock:
            item = self._item(item_id)
            ok = item["status"] in ("queued", "ready", "done") or (item["status"] == "error"
                                                                   and item["probe"] is not None)
            if not ok:
                return False
            item.update(status="running", stage="encoding", progress=0.0, eta=None, speed=None, output=None,
                        error=None)
            self._running = True
            snapshot = copy.deepcopy(item)
        self._emit("item", snapshot)
        return True

    def cancel(self, item_id: str) -> bool:
        self._record("cancel", item_id)
        with self._lock:
            item = self._item(item_id)
            if item["status"] not in ("queued", "running"):
                return False
            item.update(status="ready", progress=0.0, stage=None, eta=None, speed=None)
            snapshot = copy.deepcopy(item)
        self._emit("item", snapshot)
        return True

    def cancel_all(self) -> int:
        self._record("cancel_all")
        with self._lock:
            ids = [i for i in self._order if self._items[i]["status"] in ("queued", "running")]
        return sum(1 for i in ids if self.cancel(i))

    def get_settings(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._settings)

    def set_settings(self, patch: dict) -> dict:
        self._record("set_settings", dict(patch))
        with self._lock:
            merged = copy.deepcopy(self._settings)
            for key, value in patch.items():
                if key == "defaults" and isinstance(value, dict):
                    for kind, sub in value.items():
                        if kind in DEFAULTS and isinstance(sub, dict):
                            merged["defaults"][kind].update({k: v for k, v in sub.items() if k in DEFAULTS[kind]})
                elif key in SETTINGS:
                    merged[key] = value
            if merged["output_mode"] == "folder" and not os.path.isdir(str(merged["output_dir"] or "")):
                raise FakeUserError(MSG_OUTPUT_DIR)
            if merged["output_mode"] == "beside" and not merged["suffix"]:
                raise FakeUserError(MSG_SUFFIX)
            self._settings = merged
            snapshot = copy.deepcopy(merged)
        self._emit("settings", snapshot)
        return snapshot

    def capabilities(self) -> dict:
        return copy.deepcopy(self._caps)

    def options(self) -> dict:
        return {"video": {"formats": [{"id": "mp4", "label": "MP4", "codecs": ["h264", "h265", "av1"],
                                       "audio": True}]},
                "image": {"formats": [{"id": "jpg", "label": "JPEG", "lossy": True}]},
                "audio": {"formats": [{"id": "mp3", "label": "MP3", "lossy": True}]}}

    # -- simulated work
    def _run(self) -> None:
        while not self._stopped.wait(self.tick):
            self.advance()

    def advance(self) -> None:
        """One progress step of every running item, or the start of the first queued one when none runs (the thread
        calls it; tests may call it too)."""
        events = []
        with self._lock:
            running = [self._items[i] for i in self._order if self._items[i]["status"] == "running"]
            if not running:
                current = next((self._items[i] for i in self._order if self._items[i]["status"] == "queued"), None)
                if current is None:
                    if self._running:
                        self._running = False
                        done = sum(1 for i in self._items.values() if i["status"] == "done")
                        failed = sum(1 for i in self._items.values() if i["status"] == "error")
                        events.append(("batch_done", {"done": done, "failed": failed, "cancelled": 0}))
                else:
                    current.update(status="running", stage="encoding", progress=0.0)
                    events.append(("item", copy.deepcopy(current)))
            for current in running:
                progress = min(1.0, round(current["progress"] + self.step, 4))
                current.update(progress=progress, eta=round((1 - progress) / self.step * self.tick, 1),
                               speed="1.0x")
                if progress >= 1.0:
                    stem = Path(current["path"])
                    current.update(status="done", stage=None, eta=None, speed=None, output={
                        "path": str(stem.with_name(stem.stem + "_forge" + stem.suffix)), "size": 500_000,
                        "width": None, "height": None, "duration": None, "ratio": 0.5})
                events.append(("item", copy.deepcopy(current)))
        for name, payload in events:
            self._emit(name, payload)
