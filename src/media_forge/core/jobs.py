"""Items, their state machine, the worker threads, cancel, throttled progress events and `batch_done` (CORE.md 6).

The queue knows nothing about media: the engine passes a `runner(item, ctx) -> output dict` that does the work.
Locking: `_lock` guards the item table and the queue; `_emit_lock` serialises events so an `item` event always
carries the newest snapshot of its item (snapshots are taken inside `_emit_lock`).
"""

from __future__ import annotations

import collections
import copy
import itertools
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .errors import MSG_INTERNAL, Cancelled, UserError

log = logging.getLogger("media_forge.core.jobs")

STATUSES = ("probing", "ready", "queued", "running", "done", "error")
THROTTLE_S = 0.15
IMAGE_LANE = "image"
GENERAL_LANE = "general"

EmitFn = Callable[[str, Any], None]


@dataclass
class Item:
    id: str
    path: str
    name: str
    kind: str
    size: int | None
    settings: dict
    status: str = "probing"
    probe: dict | None = None
    thumb: str | None = None
    progress: float = 0.0
    stage: str | None = None
    eta: float | None = None
    speed: str | None = None
    output: dict | None = None
    error: str | None = None
    added_at: float = field(default_factory=time.time)
    # internal
    info: Any = None
    started_at: float | None = None
    removed: bool = False
    last_emit: float = 0.0
    pending: bool = False

    PUBLIC = ("id", "path", "name", "kind", "size", "status", "probe", "thumb", "settings", "progress", "stage",
              "eta", "speed", "output", "error", "added_at")

    def public(self) -> dict:
        data = {key: getattr(self, key) for key in self.PUBLIC}
        data["settings"] = copy.deepcopy(self.settings)
        data["probe"] = copy.deepcopy(self.probe)
        data["output"] = copy.deepcopy(self.output)
        return data

    def snapshot(self) -> "Item":
        clone = copy.copy(self)
        clone.settings = copy.deepcopy(self.settings)
        return clone


class JobContext:
    """Handed to the runner: the cancel event and `report()` for progress, stage and speed."""

    def __init__(self, queue: "JobQueue", item_id: str, job_id: str, cancel: threading.Event) -> None:
        self.queue = queue
        self.item_id = item_id
        self.job_id = job_id
        self.cancel = cancel

    def report(self, progress: float | None = None, stage: str | None = None, speed: str | None = None) -> None:
        self.queue._report(self.item_id, progress, stage, speed)


Runner = Callable[[Item, JobContext], dict]


class JobQueue:
    def __init__(self, emit: EmitFn, runner: Runner, workers: int = 1, throttle: float = THROTTLE_S,
                 image_lane: bool = True) -> None:
        self._emit_fn = emit
        self._runner = runner
        self._throttle = throttle
        self._lock = threading.RLock()
        self._cv = threading.Condition(self._lock)
        self._emit_lock = threading.RLock()
        self._items: "collections.OrderedDict[str, Item]" = collections.OrderedDict()
        self._queue: collections.deque[str] = collections.deque()
        self._running: dict[str, Item] = {}
        self._cancels: dict[str, threading.Event] = {}
        self._parallel = max(1, min(3, int(workers)))
        self._general: list[threading.Thread] = []
        self._image_lane_enabled = image_lane
        self._image_thread: threading.Thread | None = None
        self._extra: list[threading.Thread] = []  # start_now threads, one per job
        self._flusher: threading.Thread | None = None
        self._stopping = False
        self._started = False
        self._job_counter = itertools.count(1)
        self._batch = {"done": 0, "failed": 0, "cancelled": 0}

    # --- events --------------------------------------------------------------------------------------------
    def _emit(self, name: str, payload: Any) -> None:
        try:
            self._emit_fn(name, payload)
        except Exception:  # noqa: BLE001  a broken listener must not stop a worker
            log.exception("event listener failed for %s", name)

    def emit_item(self, item_id: str) -> None:
        with self._emit_lock:
            with self._lock:
                item = self._items.get(item_id)
                if item is None or item.removed:
                    return
                item.last_emit = time.monotonic()
                item.pending = False
                snap = item.public()
            self._emit("item", snap)

    # --- lifecycle -----------------------------------------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._stopping = False
            self._spawn_workers()
            self._flusher = threading.Thread(target=self._flush_loop, name="jobs-flush", daemon=True)
            self._flusher.start()

    def _spawn_workers(self) -> None:
        self._general = [t for t in self._general if t.is_alive()]
        while len(self._general) < self._parallel:
            index = len(self._general)
            thread = threading.Thread(target=self._worker, args=(GENERAL_LANE, index), daemon=True,
                                      name=f"jobs-{index + 1}")
            self._general.append(thread)
            thread.start()
        if self._image_lane_enabled and (self._image_thread is None or not self._image_thread.is_alive()):
            self._image_thread = threading.Thread(target=self._worker, args=(IMAGE_LANE, 0), daemon=True,
                                                  name="jobs-image")
            self._image_thread.start()

    def set_parallel(self, workers: int) -> None:
        with self._cv:
            self._parallel = max(1, min(3, int(workers)))
            if self._started and not self._stopping:
                self._spawn_workers()
            self._cv.notify_all()

    def stop(self, timeout: float = 15.0) -> None:
        with self._cv:
            self._stopping = True
            for event in self._cancels.values():
                event.set()
            self._cv.notify_all()
            threads = list(self._general) + ([self._image_thread] if self._image_thread else []) + list(self._extra)
        deadline = time.monotonic() + timeout
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        with self._lock:
            self._started = False

    # --- item table ----------------------------------------------------------------------------------------
    def add(self, item: Item) -> dict:
        with self._lock:
            self._items[item.id] = item
            snap = item.public()
        return snap

    def get(self, item_id: str) -> Item | None:
        with self._lock:
            item = self._items.get(item_id)
            return item.snapshot() if item else None

    def public(self, item_id: str) -> dict | None:
        with self._lock:
            item = self._items.get(item_id)
            return item.public() if item else None

    def list_public(self) -> list[dict]:
        with self._lock:
            return [item.public() for item in self._items.values()]

    def items(self) -> list[Item]:
        with self._lock:
            return [item.snapshot() for item in self._items.values()]

    def find_path(self, key: str, normalize: Callable[[str], str]) -> str | None:
        with self._lock:
            for item in self._items.values():
                if normalize(item.path) == key:
                    return item.id
        return None

    def update(self, item_id: str, emit: bool = True, **fields: Any) -> dict | None:
        """Set item fields (from the probe or the engine) and emit the item."""
        with self._lock:
            item = self._items.get(item_id)
            if item is None:
                return None
            for key, value in fields.items():
                setattr(item, key, value)
            snap = item.public()
        if emit:
            self.emit_item(item_id)
        return snap

    def remove(self, item_id: str) -> bool:
        """Remove an item; a running one is cancelled first (the worker drops it when it stops)."""
        with self._cv:
            item = self._items.pop(item_id, None)
            if item is None:
                return False
            item.removed = True
            if item_id in self._queue:
                self._queue.remove(item_id)
            event = self._cancels.get(item_id)
            if event is not None:
                event.set()
            self._cv.notify_all()
        with self._emit_lock:
            self._emit("item_removed", {"id": item_id})
        self._check_batch_done()
        return True

    # --- queue control -------------------------------------------------------------------------------------
    def start_items(self, ids: Iterable[str] | None) -> list[str]:
        """Queue items in list order. `None` = every ready item; explicit ids also re-run done / failed items."""
        queued: list[str] = []
        with self._cv:
            wanted = None if ids is None else set(ids)
            for item in self._items.values():
                if wanted is None:
                    ok = item.status == "ready"
                else:
                    ok = item.id in wanted and (item.status in ("ready", "done")
                                                or (item.status == "error" and item.info is not None))
                if not ok:
                    continue
                item.status = "queued"
                item.progress, item.stage, item.eta, item.speed = 0.0, None, None, None
                item.output, item.error = None, None
                self._queue.append(item.id)
                queued.append(item.id)
            self._cv.notify_all()
        for item_id in queued:
            self.emit_item(item_id)
        return queued

    def start_now(self, item_id: str) -> bool:
        """Run one item at once on an extra thread that ends with the job, outside `parallel_jobs` and the lanes.

        A queued item leaves the queue; a ready, done or failed (probed) item is reset as by `start_items`.
        False for an unknown, probing or running item, a failed probe, or a stopping queue.
        """
        with self._cv:
            item = self._items.get(item_id)
            if item is None or item.removed or self._stopping:
                return False
            if item.status == "queued":
                if item_id in self._queue:
                    self._queue.remove(item_id)
            elif item.status in ("ready", "done") or (item.status == "error" and item.info is not None):
                item.output, item.error = None, None
            else:
                return False
            job = self._claim(item_id)
            thread = threading.Thread(target=self._execute, args=job, daemon=True,
                                      name=f"jobs-now-{job[2]}")
            self._extra = [t for t in self._extra if t.is_alive()]
            self._extra.append(thread)
            self._cv.notify_all()
        thread.start()
        return True

    def cancel(self, item_id: str) -> bool:
        with self._cv:
            item = self._items.get(item_id)
            if item is None:
                return False
            if item.status == "queued":
                if item_id in self._queue:
                    self._queue.remove(item_id)
                item.status, item.progress, item.stage, item.eta, item.speed = "ready", 0.0, None, None, None
                self._batch["cancelled"] += 1
                result = True
            elif item.status == "running":
                event = self._cancels.get(item_id)
                if event is not None:
                    event.set()
                return True
            else:
                return False
        if result:
            self.emit_item(item_id)
            self._check_batch_done()
        return result

    def cancel_all(self) -> int:
        with self._lock:
            ids = [item.id for item in self._items.values() if item.status in ("queued", "running")]
        return sum(1 for item_id in ids if self.cancel(item_id))

    def busy(self) -> bool:
        with self._lock:
            return bool(self._queue) or bool(self._running)

    def wait_idle(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cv:
            while self._queue or self._running:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._cv.wait(0.2 if remaining is None else min(0.2, remaining))
        return True

    # --- progress ------------------------------------------------------------------------------------------
    def _report(self, item_id: str, progress: float | None, stage: str | None, speed: str | None) -> None:
        now = time.monotonic()
        with self._lock:
            item = self._running.get(item_id)
            if item is None or item.removed:
                return
            stage_changed = stage is not None and stage != item.stage
            if stage is not None:
                item.stage = stage
            if progress is not None:
                item.progress = round(max(0.0, min(1.0, float(progress))), 4)
            if speed is not None or stage_changed:
                item.speed = speed
            if item.started_at and item.progress > 0.02:
                elapsed = time.time() - item.started_at
                item.eta = round(max(0.0, elapsed * (1.0 - item.progress) / item.progress), 1)
            due = stage_changed or now - item.last_emit >= self._throttle
            if not due:
                item.pending = True
                return
        self.emit_item(item_id)

    def _flush_loop(self) -> None:
        while True:
            time.sleep(self._throttle / 2)
            with self._lock:
                if self._stopping and not self._running:
                    return
                now = time.monotonic()
                due = [i.id for i in self._items.values() if i.pending and now - i.last_emit >= self._throttle]
            for item_id in due:
                self.emit_item(item_id)

    # --- workers -------------------------------------------------------------------------------------------
    def _pick(self, lane: str) -> str | None:
        for item_id in self._queue:
            item = self._items.get(item_id)
            if item is None:
                continue
            if lane == IMAGE_LANE and item.kind != "image":
                continue
            return item_id
        return None

    def _worker(self, lane: str, index: int) -> None:
        while True:
            with self._cv:
                while True:
                    if self._stopping:
                        return
                    if lane == GENERAL_LANE and index >= self._parallel:
                        return
                    item_id = self._pick(lane)
                    if item_id is not None:
                        break
                    self._cv.wait(0.5)
                self._queue.remove(item_id)
                job = self._claim(item_id)
            self._execute(*job)

    def _claim(self, item_id: str) -> tuple[Item, Item, str, threading.Event]:
        """Mark an item running (caller holds `_lock`; the item is not in the queue any more)."""
        item = self._items[item_id]
        item.status = "running"
        item.started_at = time.time()
        item.progress, item.stage, item.eta, item.speed = 0.0, None, None, None
        cancel = threading.Event()
        self._cancels[item_id] = cancel
        self._running[item_id] = item
        job_id = f"{item_id}-{next(self._job_counter)}"
        return item, item.snapshot(), job_id, cancel

    def _execute(self, item: Item, snapshot: Item, job_id: str, cancel: threading.Event) -> None:
        """Run one claimed job to its end on the calling thread and record the outcome."""
        item_id = item.id
        self.emit_item(item_id)
        ctx = JobContext(self, item_id, job_id, cancel)
        status, output, error = "error", None, None
        try:
            output = self._runner(snapshot, ctx)
            status = "done"
            if cancel.is_set():
                raise Cancelled()
        except Cancelled:
            status, output = "cancelled", None
        except UserError as exc:
            status, error = "error", exc.message
        except Exception:  # noqa: BLE001
            log.exception("job %s failed", job_id)
            status, error = "error", MSG_INTERNAL
        with self._cv:
            self._running.pop(item_id, None)
            self._cancels.pop(item_id, None)
            item.pending = False
            if status == "cancelled":
                self._batch["cancelled"] += 1
                item.status, item.progress, item.stage, item.eta, item.speed = "ready", 0.0, None, None, None
            elif status == "done":
                self._batch["done"] += 1
                item.status, item.progress, item.stage, item.eta, item.speed = "done", 1.0, None, None, None
                item.output = output
            else:
                self._batch["failed"] += 1
                item.status, item.stage, item.eta, item.speed = "error", None, None, None
                item.error = error
            self._cv.notify_all()
        self.emit_item(item_id)
        self._check_batch_done()

    def _check_batch_done(self) -> None:
        with self._emit_lock:
            with self._lock:
                if self._queue or self._running or sum(self._batch.values()) == 0:
                    return
                counts, self._batch = self._batch, {"done": 0, "failed": 0, "cancelled": 0}
            self._emit("batch_done", counts)
