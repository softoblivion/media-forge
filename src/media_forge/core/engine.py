"""The facade the shell's bridge calls (ARCHITECTURE.md 6, BRIDGE_API.md 2 and 4).

Thread model: the bridge thread calls the public methods; probe threads, worker threads and the tool discovery
thread call back into the engine. Every shared structure has its own lock (settings, job queue, capabilities);
events go out through `emit(name, payload)`, which the shell turns into `window.mediaForge.dispatch`.
"""

from __future__ import annotations

import copy
import itertools
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from . import audio as audio_mod
from . import catalog, ffjob, image as image_mod, probe as probe_mod, procs, tools, upscale, video as video_mod
from .errors import (MSG_BUSY, MSG_NO_FFMPEG, MSG_NOT_FOUND, MSG_OUTPUT_DIR_MISSING, MSG_UNKNOWN_ITEM,
                     MSG_WRITE_DENIED, Cancelled, UserError)
from .jobs import Item, JobContext, JobQueue
from .paths import AppPaths, remove_path
from .settings import Settings, folder_usable

log = logging.getLogger("media_forge.core.engine")

EmitFn = Callable[[str, Any], None]
PROBE_WORKERS = 3
CLEAR_KINDS = ("done", "errors", "idle", "all")
MSG_FFMPEG_DOWNLOAD = "ffmpeg could not be downloaded; video and audio files cannot be processed."


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


class Engine:
    def __init__(self, emit: EmitFn | None = None, version: str = "0.0.0", *, paths: AppPaths | None = None,
                 persist_settings: bool = True, allow_download: bool = True, use_path_ffmpeg: bool = True) -> None:
        self._emit_fn = emit or (lambda name, payload: None)
        self.version = version
        self.paths = paths or AppPaths()
        self.settings = Settings(self.paths, persist=persist_settings)
        self.allow_download = allow_download
        self.use_path_ffmpeg = use_path_ffmpeg
        self._caps_lock = threading.RLock()
        self._caps: dict = {
            "ffmpeg": "probing", "ffmpeg_version": None, "ffmpeg_path": None,
            "nvenc": {"h264": False, "h265": False, "av1": False}, "gpu_name": None,
            "avif": tools.avif_supported(), "heif": catalog.heif_supported(),
            "models": upscale.model_status(self.paths), "encoders": [],
        }
        self._caps_known = False
        self._tool: tools.FFmpeg | None = None
        self._tools_ready = threading.Event()
        self._stop = threading.Event()
        self._ids = itertools.count(1)
        self._id_lock = threading.Lock()
        self.queue = JobQueue(self._emit, self._run_job, workers=1)
        self._probe_pool: ThreadPoolExecutor | None = None
        self._started = False

    # --- events --------------------------------------------------------------------------------------------
    def _emit(self, name: str, payload: Any) -> None:
        try:
            self._emit_fn(name, payload)
        except Exception:  # noqa: BLE001
            log.exception("event listener failed for %s", name)

    def _toast(self, kind: str, message: str) -> None:
        self._emit("toast", {"kind": kind, "message": message})

    # --- lifecycle -----------------------------------------------------------------------------------------
    def start(self) -> None:
        """Load settings, start the workers and discover the tools in the background."""
        if self._started:
            return
        self._started = True
        self.paths.ensure()
        self.paths.clear_cache()
        self.settings.load()
        self.queue.set_parallel(self.settings.value("parallel_jobs"))
        self.queue.start()
        self._probe_pool = ThreadPoolExecutor(max_workers=PROBE_WORKERS, thread_name_prefix="probe")
        threading.Thread(target=self._discover, name="tools", daemon=True).start()

    def stop(self) -> None:
        """Cancel every job, stop the workers and the probe threads."""
        self._stop.set()
        self.queue.cancel_all()
        self.queue.stop()
        if self._probe_pool is not None:
            self._probe_pool.shutdown(wait=False, cancel_futures=True)
        self.settings.save()

    def _set_caps(self, **fields: Any) -> None:
        with self._caps_lock:
            self._caps.update(fields)
        self._emit("capabilities", self.capabilities())

    def _discover(self) -> None:
        try:
            tool = tools.find_ffmpeg(self.paths, use_path=self.use_path_ffmpeg)
            if tool is None and self.allow_download:
                self._set_caps(ffmpeg="downloading")
                try:
                    tool = tools.download_ffmpeg(self.paths, cancel=self._stop)
                except Exception as exc:  # noqa: BLE001
                    log.warning("ffmpeg download failed: %s", exc)
                    tool = None
                    self._toast("error", MSG_FFMPEG_DOWNLOAD)
            caps = tools.probe_capabilities(tool)
            caps["models"] = upscale.model_status(self.paths)
            with self._caps_lock:
                self._tool = tool
                self._caps = caps
                self._caps_known = True
        except Exception:  # noqa: BLE001
            log.exception("tool discovery failed")
            with self._caps_lock:
                self._caps["ffmpeg"] = "missing"
                self._caps_known = True
        finally:
            self._tools_ready.set()
        self._emit("capabilities", self.capabilities())
        caps = self._caps_for_rules()
        if self.settings.renormalize_defaults(caps):
            self._emit("settings", self.settings.get())
        for item in self.queue.items():
            if item.status == "running":
                continue
            fixed = catalog.normalize(item.kind, item.settings, caps)
            if fixed != item.settings:
                self.queue.update(item.id, settings=fixed)

    def wait_tools(self, timeout: float | None = None) -> bool:
        return self._tools_ready.wait(timeout)

    def _caps_for_rules(self) -> dict | None:
        with self._caps_lock:
            return copy.deepcopy(self._caps) if self._caps_known else None

    # --- state ---------------------------------------------------------------------------------------------
    def state(self) -> dict:
        """`State` without `update` and `window`, which the shell adds (BRIDGE_API.md 3.1)."""
        return {"version": self.version, "settings": self.settings.get(), "items": self.queue.list_public(),
                "capabilities": self.capabilities(), "options": self.options()}

    def capabilities(self) -> dict:
        with self._caps_lock:
            return copy.deepcopy(tools.public_capabilities(self._caps))

    def options(self) -> dict:
        with self._caps_lock:
            caps = copy.deepcopy(self._caps)
        return catalog.options(caps)

    def item(self, item_id: str) -> dict | None:
        return self.queue.public(item_id)

    # --- items ---------------------------------------------------------------------------------------------
    def _next_id(self) -> str:
        with self._id_lock:
            return f"i{next(self._ids)}"

    def add_files(self, paths: Iterable[str]) -> list[dict]:
        added: list[dict] = []
        duplicates = 0
        seen: set[str] = set()
        for raw in paths:
            if not raw:
                continue
            path = os.path.abspath(str(raw))
            key = _path_key(path)
            if key in seen or self.queue.find_path(key, _path_key) is not None:
                duplicates += 1
                continue
            seen.add(key)
            kind = catalog.kind_for_extension(path) or "video"
            try:
                size = os.path.getsize(path)
            except OSError:
                size = None
            item = Item(id=self._next_id(), path=path, name=os.path.basename(path), kind=kind, size=size,
                        settings=self.settings.item_defaults(kind))
            added.append(self.queue.add(item))
            if self._probe_pool is not None:
                self._probe_pool.submit(self._probe_item, item.id)
            else:
                threading.Thread(target=self._probe_item, args=(item.id,), daemon=True).start()
        if duplicates == 1:
            self._toast("info", "The file is already in the list.")
        elif duplicates > 1:
            self._toast("info", f"{duplicates} files are already in the list.")
        return added

    def _probe_item(self, item_id: str) -> None:
        item = self.queue.get(item_id)
        if item is None:
            return
        try:
            if catalog.kind_for_extension(item.path) != "image":
                while not self._tools_ready.wait(0.2):
                    if self._stop.is_set() or self.queue.get(item_id) is None:
                        return
            tool = self._tool
            info = probe_mod.probe(item.path, tool.ffprobe if tool else None)
            settings = item.settings
            current = self.queue.get(item_id)
            if current is None:
                return
            if info.kind != current.kind:
                settings = catalog.normalize(info.kind, self.settings.item_defaults(info.kind),
                                             self._caps_for_rules())
            else:
                settings = current.settings
            thumb = probe_mod.thumbnail(item.path, info.kind, info, tool.ffmpeg if tool else None)
            try:
                size = os.path.getsize(item.path)
            except OSError:
                size = item.size
            self.queue.update(item_id, kind=info.kind, settings=settings, probe=info.public(), info=info,
                              thumb=thumb, size=size, status="ready", error=None)
        except UserError as exc:
            self.queue.update(item_id, status="error", error=exc.message, probe=None, info=None)
        except Exception:  # noqa: BLE001
            log.exception("probe failed for %s", item.path)
            self.queue.update(item_id, status="error", error="The file could not be read.", probe=None, info=None)

    def wait_probed(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        while any(i.status == "probing" for i in self.queue.items()):
            if deadline is not None and time.monotonic() > deadline:
                return False
            time.sleep(0.05)
        return True

    def remove_item(self, item_id: str) -> bool:
        return self.queue.remove(item_id)

    def clear(self, what: str) -> list[str]:
        if what not in CLEAR_KINDS:
            what = "done"
        removed: list[str] = []
        for item in self.queue.items():
            if what == "done":
                hit = item.status == "done"
            elif what == "errors":
                hit = item.status == "error"
            elif what == "idle":
                hit = item.status not in ("queued", "running")
            else:
                hit = True
            if hit and self.queue.remove(item.id):
                removed.append(item.id)
        return removed

    def set_item_settings(self, item_id: str, patch: Mapping[str, Any]) -> dict:
        item = self.queue.get(item_id)
        if item is None:
            raise UserError(MSG_UNKNOWN_ITEM)
        if item.status == "running":
            raise UserError(MSG_BUSY)
        new = catalog.merge(item.kind, item.settings, patch if isinstance(patch, Mapping) else {},
                            self._caps_for_rules())
        result = self.queue.update(item_id, settings=new)
        if result is None:
            raise UserError(MSG_UNKNOWN_ITEM)
        return result

    def apply_to_kind(self, item_id: str) -> list[dict]:
        source = self.queue.get(item_id)
        if source is None:
            raise UserError(MSG_UNKNOWN_ITEM)
        changed: list[dict] = []
        for item in self.queue.items():
            if item.id == source.id or item.kind != source.kind or item.status == "running":
                continue
            if item.settings != source.settings:
                result = self.queue.update(item.id, settings=copy.deepcopy(source.settings))
                if result is not None:
                    changed.append(result)
        return changed

    def start_items(self, ids: Iterable[str] | None) -> list[str]:
        return self.queue.start_items(None if ids is None else list(ids))

    def cancel(self, item_id: str) -> bool:
        return self.queue.cancel(item_id)

    def cancel_all(self) -> int:
        return self.queue.cancel_all()

    def wait_idle(self, timeout: float | None = None) -> bool:
        return self.queue.wait_idle(timeout)

    # --- settings ------------------------------------------------------------------------------------------
    def get_settings(self) -> dict:
        return self.settings.get()

    def set_settings(self, patch: Mapping[str, Any]) -> dict:
        try:
            result = self.settings.patch(patch, self._caps_for_rules())
        except UserError:
            self._emit("settings", self.settings.get())
            raise
        self.queue.set_parallel(result["parallel_jobs"])
        self._emit("settings", result)
        return result

    # --- jobs ----------------------------------------------------------------------------------------------
    def _wait_ffmpeg(self, ctx: JobContext) -> tools.FFmpeg:
        while not self._tools_ready.wait(0.2):
            if ctx.cancel.is_set():
                raise Cancelled()
        if self._tool is None:
            raise UserError(MSG_NO_FFMPEG)
        return self._tool

    def _get_model(self, name: str, scale: int, ctx: JobContext) -> upscale.Model:
        if not upscale.model_present(name, scale, self.paths):
            ctx.report(progress=0.0, stage="downloading model")
            with self._caps_lock:
                self._caps.setdefault("models", {})[name] = "downloading"
            self._emit("capabilities", self.capabilities())

            def on_progress(done: int, total: int | None) -> None:
                if total:
                    ctx.report(progress=min(1.0, done / total), stage="downloading model")

            try:
                upscale.ensure_model(name, scale, self.paths, on_progress, ctx.cancel)
            except UserError:
                self._set_caps(models={**upscale.model_status(self.paths), name: "error"})
                raise
            except Cancelled:
                self._set_caps(models=upscale.model_status(self.paths))
                raise
            self._set_caps(models=upscale.model_status(self.paths))
        return upscale.Model.load(name, scale, self.paths)

    def plan(self, item_id: str) -> str:
        """The command lines a job would run, for the CLI (`ffjob.Plan.describe`)."""
        item = self.queue.get(item_id)
        if item is None or item.info is None:
            raise UserError(MSG_UNKNOWN_ITEM)
        if item.kind == "image":
            return f"# Pillow: {item.settings}"
        tool = self._tool
        if tool is None:
            raise UserError(MSG_NO_FFMPEG)
        out = catalog.output_path(item.path, item.kind, item.settings, self.settings.get())
        workdir = self.paths.jobs_dir / "plan"
        caps = self._caps_for_rules()
        if item.kind == "video":
            plan = video_mod.plan_video(Path(item.path), item.info, item.settings, caps, tool.ffmpeg, out, workdir)
        else:
            plan = audio_mod.plan_audio(Path(item.path), item.info, item.settings, tool.ffmpeg, out)
        return plan.describe()

    def _run_job(self, item: Item, ctx: JobContext) -> dict:
        global_settings = self.settings.get()
        if global_settings["output_mode"] == "folder" and not folder_usable(global_settings["output_dir"]):
            raise UserError(MSG_OUTPUT_DIR_MISSING)
        caps = self._caps_for_rules()
        settings = catalog.normalize(item.kind, item.settings, caps)
        src = Path(item.path)
        if not src.is_file():
            raise UserError(MSG_NOT_FOUND)
        info = item.info
        tool = self._wait_ffmpeg(ctx) if item.kind != "image" else self._tool
        try:
            out = catalog.output_path(src, item.kind, settings, global_settings, reserve=True)
        except OSError as exc:
            raise UserError(MSG_WRITE_DENIED) from exc
        workdir = self.paths.job_cache(ctx.job_id)
        ok = False
        try:
            result: dict = {}
            if item.kind == "image":
                dims = image_mod.run_image(src, info, settings, out, ctx,
                                           get_model=lambda name, scale: self._get_model(name, scale, ctx))
                result.update(dims)
            elif item.kind == "video":
                model = None
                factor = video_mod.FACTORS.get(settings["scale"])
                if (factor and settings["upscale_method"] in ("fsrcnn", "edsr")
                        and settings["format"] not in catalog.AUDIO_ONLY_VIDEO_FORMATS):
                    model = self._get_model(settings["upscale_method"], factor, ctx)
                video_mod.run_video(src, info, settings, caps, tool.ffmpeg, out, workdir, ctx, model)
            else:
                audio_mod.run_audio(src, info, settings, tool.ffmpeg, out, ctx)
            if ctx.cancel.is_set():
                raise Cancelled()
            output = self._describe_output(out, item, result, tool)
            ok = True
            return output
        except ffjob.FFmpegError as exc:
            self._write_job_log(ctx.job_id, item, exc)
            raise UserError(ffjob.error_message(exc.tail)) from exc
        except OSError as exc:
            log.warning("job %s: %s", ctx.job_id, exc)
            if getattr(exc, "errno", None) == 28:
                raise UserError(ffjob.error_message(["No space left on device"])) from exc
            raise UserError(MSG_WRITE_DENIED) from exc
        finally:
            if not ok:
                remove_path(out)
            remove_path(workdir)

    def _describe_output(self, out: Path, item: Item, result: dict, tool: tools.FFmpeg | None) -> dict:
        size = out.stat().st_size
        width, height, duration = result.get("width"), result.get("height"), None
        if item.kind != "image" and tool is not None:
            data = probe_mod.ffprobe_json(out, tool.ffprobe)
            info = probe_mod.info_from_ffprobe(data, size) if data else None
            if info is not None:
                duration = info.duration
                if info.has_video and not catalog.is_audio_only(item.kind, item.settings):
                    width, height = info.width, info.height
        source_size = item.size or 0
        ratio = round(size / source_size, 4) if source_size else None
        return {"path": str(out), "size": size, "width": width, "height": height, "duration": duration,
                "ratio": ratio}

    def _write_job_log(self, job_id: str, item: Item, exc: ffjob.FFmpegError) -> None:
        try:
            lines = [f"item: {item.path}", f"exit code: {exc.returncode}",
                     "command: " + procs.format_cmd(exc.args_list), "stderr (last lines):"]
            lines += exc.tail
            self.paths.job_log(job_id).write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            log.exception("job log could not be written")
