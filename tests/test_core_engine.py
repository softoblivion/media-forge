"""engine and jobs: add / probe / settings / start / cancel / remove / clear, events, throttling, batch_done."""

from __future__ import annotations

import threading
import time
import unittest

from media_forge.core import catalog
from media_forge.core.engine import Engine
from media_forge.core.errors import Cancelled, UserError
from media_forge.core.jobs import Item, JobQueue
from media_forge.core.paths import AppPaths

from tests.core_support import ScratchCase, media, needs_ffmpeg


class Recorder:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.events: list[tuple[str, dict]] = []

    def __call__(self, name, payload) -> None:
        with self.lock:
            self.events.append((name, payload))

    def named(self, name: str) -> list[dict]:
        with self.lock:
            return [p for n, p in self.events if n == name]


def wait_for(predicate, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@needs_ffmpeg
class EngineTest(ScratchCase):
    def setUp(self) -> None:
        self.rec = Recorder()
        home = self.out / f"home_{self._testMethodName}"
        self.engine = Engine(self.rec, "0.1.0", paths=AppPaths(home=home), allow_download=False)
        self.engine.start()
        self.assertTrue(self.engine.wait_tools(60))
        self.engine.set_settings({"output_mode": "folder", "output_dir": str(self.out)})

    def tearDown(self) -> None:
        self.engine.stop()

    def add(self, *names: str) -> list[dict]:
        items = self.engine.add_files([str(media(n)) for n in names])
        self.assertTrue(self.engine.wait_probed(60))
        return [self.engine.item(i["id"]) for i in items]

    def test_state_and_capabilities(self):
        state = self.engine.state()
        self.assertEqual(set(state), {"version", "settings", "items", "capabilities", "options"})
        caps = state["capabilities"]
        self.assertEqual(caps["ffmpeg"], "ready")
        self.assertEqual(set(caps), {"ffmpeg", "ffmpeg_version", "ffmpeg_path", "nvenc", "gpu_name", "avif", "heif",
                                     "models"})
        self.assertEqual(caps["models"]["fsrcnn"], "ready")
        self.assertTrue(self.rec.named("capabilities"))
        self.assertEqual(state["options"]["video"]["formats"][0]["id"], "mp4")

    def test_add_probe_kinds_and_duplicates(self):
        items = self.add("clip.mp4", "photo.jpg", "tone.mp3", "anim.gif", "audio_only.mp4", "notmedia.mp4")
        kinds = [(i["kind"], i["status"]) for i in items]
        self.assertEqual(kinds, [("video", "ready"), ("image", "ready"), ("audio", "ready"), ("video", "ready"),
                                 ("audio", "ready"), ("video", "error")])
        self.assertEqual(items[5]["error"], "Not a media file.")
        self.assertIsNone(items[5]["probe"])
        self.assertEqual(set(items[4]["settings"]), set(catalog.DEFAULTS["audio"]))  # kind changed by the probe
        for item in items[:5]:
            self.assertTrue(item["thumb"].startswith("data:image/"))
        again = self.engine.add_files([str(media("clip.mp4"))])
        self.assertEqual(again, [])
        self.assertEqual(self.rec.named("toast")[-1], {"kind": "info", "message": "The file is already in the list."})

    def test_animated_gif_added_before_ffmpeg_is_found(self):
        """A file that only ffprobe can read waits for the tool discovery instead of ending in an error."""
        from unittest import mock

        from media_forge.core import tools
        release = threading.Event()
        real_find = tools.find_ffmpeg

        def slow_find(*args, **kwargs):
            release.wait(30)
            return real_find(*args, **kwargs)

        rec = Recorder()
        engine = Engine(rec, "0.1.0", paths=AppPaths(home=self.out / "home_slow_tools"), allow_download=False)
        with mock.patch.object(tools, "find_ffmpeg", slow_find):
            engine.start()
            try:
                item = engine.add_files([str(media("anim.gif")), str(media("photo.jpg"))])
                self.assertTrue(wait_for(lambda: engine.item(item[1]["id"])["status"] == "ready", 30))
                self.assertEqual(engine.item(item[0]["id"])["status"], "probing")  # a still does not wait
                release.set()
                self.assertTrue(engine.wait_probed(60))
                gif = engine.item(item[0]["id"])
                self.assertEqual((gif["kind"], gif["status"], gif["error"]), ("video", "ready", None))
            finally:
                release.set()
                engine.stop()

    def test_animated_gif_when_ffmpeg_is_found_during_its_probe(self):
        """The discovery that ends between the probe without ffprobe and its error still leads to a second probe."""
        from unittest import mock

        from media_forge.core import engine as engine_mod
        from media_forge.core import tools
        release = threading.Event()
        real_find, real_probe = tools.find_ffmpeg, engine_mod.probe_mod.probe

        def slow_find(*args, **kwargs):
            release.wait(30)
            return real_find(*args, **kwargs)

        rec = Recorder()
        engine = Engine(rec, "0.1.0", paths=AppPaths(home=self.out / "home_race_tools"), allow_download=False)

        def racing_probe(path, ffprobe):
            if ffprobe is None:  # the discovery finishes while this probe runs
                release.set()
                engine.wait_tools(60)
            return real_probe(path, ffprobe)

        with mock.patch.object(tools, "find_ffmpeg", slow_find), \
                mock.patch.object(engine_mod.probe_mod, "probe", racing_probe):
            engine.start()
            try:
                item = engine.add_files([str(media("anim.gif"))])
                self.assertTrue(engine.wait_probed(60))
                gif = engine.item(item[0]["id"])
                self.assertEqual((gif["kind"], gif["status"], gif["error"]), ("video", "ready", None))
            finally:
                release.set()
                engine.stop()

    def test_run_video_image_audio(self):
        items = self.add("clip.mp4", "photo.jpg", "tone.mp3")
        self.engine.set_item_settings(items[0]["id"], {"encoder": "cpu", "quality": 50})
        self.engine.set_item_settings(items[1]["id"], {"format": "webp"})
        queued = self.engine.start_items(None)
        self.assertEqual(queued, [i["id"] for i in items])
        self.assertTrue(self.engine.wait_idle(120))
        for item in items:
            final = self.engine.item(item["id"])
            self.assertEqual(final["status"], "done", final["error"])
            out = final["output"]
            self.assertTrue(out["path"].startswith(str(self.out)))
            self.assertGreater(out["size"], 0)
            self.assertIsNotNone(out["ratio"])
        video = self.engine.item(items[0]["id"])["output"]
        self.assertEqual((video["width"], video["height"]), (320, 240))
        self.assertAlmostEqual(video["duration"], 2.0, delta=0.1)
        audio = self.engine.item(items[2]["id"])["output"]
        self.assertIsNone(audio["width"])
        self.assertTrue(wait_for(lambda: self.rec.named("batch_done")))
        self.assertEqual(self.rec.named("batch_done")[-1], {"done": 3, "failed": 0, "cancelled": 0})
        statuses = [p["status"] for p in self.rec.named("item") if p["id"] == items[0]["id"]]
        self.assertIn("queued", statuses)
        self.assertIn("running", statuses)
        self.assertEqual(statuses[-1], "done")
        self.assertFalse(list(self.engine.paths.jobs_dir.iterdir()))  # job scratch removed

    def test_progress_events_throttled(self):
        items = self.add("clip_long.mp4")
        self.engine.set_item_settings(items[0]["id"], {"encoder": "cpu", "format": "webm", "codec": "vp9"})
        start = time.monotonic()
        self.engine.start_items([items[0]["id"]])
        self.assertTrue(self.engine.wait_idle(300))
        elapsed = time.monotonic() - start
        running = [p for p in self.rec.named("item") if p["id"] == items[0]["id"] and p["status"] == "running"]
        self.assertGreater(len(running), 1)
        self.assertLessEqual(len(running), elapsed / 0.15 + 5)
        self.assertTrue(any(p["speed"] for p in running))
        self.assertTrue(any(p["eta"] is not None for p in running))

    def test_cancel_running_deletes_partial(self):
        items = self.add("clip_long.mp4")
        item_id = items[0]["id"]
        self.engine.set_item_settings(item_id, {"encoder": "cpu", "codec": "h265", "quality": 95})
        self.engine.start_items([item_id])
        self.assertTrue(wait_for(lambda: (self.engine.item(item_id)["progress"] or 0) > 0.02, 120))
        with self.assertRaises(UserError) as cm:
            self.engine.set_item_settings(item_id, {"quality": 10})
        self.assertEqual(cm.exception.message, "The file is being processed.")
        self.assertTrue(self.engine.cancel(item_id))
        self.assertTrue(self.engine.wait_idle(30))
        final = self.engine.item(item_id)
        self.assertEqual((final["status"], final["progress"], final["output"]), ("ready", 0.0, None))
        self.assertFalse(list(self.out.glob("clip_long_forge*")))
        self.assertTrue(wait_for(lambda: self.rec.named("batch_done")))
        self.assertEqual(self.rec.named("batch_done")[-1]["cancelled"], 1)

    def test_error_item_and_job_message(self):
        items = self.add("noaudio.mkv")
        self.engine.set_item_settings(items[0]["id"], {"format": "mp3"})
        self.engine.start_items(None)
        self.assertTrue(self.engine.wait_idle(60))
        final = self.engine.item(items[0]["id"])
        self.assertEqual(final["status"], "error")
        self.assertEqual(final["error"], "The file has no audio track.")
        self.assertFalse(list(self.out.glob("noaudio_forge*")))

    def test_settings_apply_remove_clear(self):
        items = self.add("photo.jpg", "photo.png", "clip.mp4")
        changed = self.engine.set_item_settings(items[0]["id"], {"format": "png", "quality": 40, "junk": 1})
        self.assertEqual((changed["settings"]["format"], changed["settings"]["quality"]), ("png", 40))
        self.assertNotIn("junk", changed["settings"])
        result = self.engine.apply_to_kind(items[0]["id"])
        self.assertEqual([r["id"] for r in result], [items[1]["id"]])
        self.assertEqual(self.engine.item(items[1]["id"])["settings"]["quality"], 40)
        with self.assertRaises(UserError):
            self.engine.set_item_settings("nope", {})
        self.assertTrue(self.engine.remove_item(items[2]["id"]))
        self.assertEqual(self.rec.named("item_removed")[-1], {"id": items[2]["id"]})
        self.assertIsNone(self.engine.item(items[2]["id"]))
        self.engine.start_items([items[0]["id"]])
        self.assertTrue(self.engine.wait_idle(60))
        self.assertEqual(self.engine.clear("done"), [items[0]["id"]])
        self.assertEqual(self.engine.clear("all"), [items[1]["id"]])
        self.assertEqual(self.engine.state()["items"], [])

    def test_settings_events_and_refusal(self):
        before = len(self.rec.named("settings"))
        with self.assertRaises(UserError):
            self.engine.set_settings({"output_mode": "folder", "output_dir": str(self.out / "missing")})
        self.assertEqual(len(self.rec.named("settings")), before + 1)
        self.assertEqual(self.rec.named("settings")[-1]["output_dir"], str(self.out))
        result = self.engine.set_settings({"parallel_jobs": 2, "defaults": {"video": {"format": "webm"}}})
        self.assertEqual(result["defaults"]["video"]["codec"], "vp9")
        items = self.add("clip_long.mp4")
        self.assertEqual(items[0]["settings"]["format"], "webm")

    def test_retry_after_error_and_run_again_after_done(self):
        folder = self.out / "retry"
        folder.mkdir(exist_ok=True)
        for old in folder.iterdir():
            old.unlink()
        self.engine.set_settings({"output_mode": "folder", "output_dir": str(folder)})
        folder.rmdir()
        items = self.add("photo.jpg")
        item_id = items[0]["id"]
        self.assertEqual(self.engine.start_items(None), [item_id])
        self.assertTrue(self.engine.wait_idle(30))
        self.assertEqual(self.engine.item(item_id)["status"], "error")
        self.assertEqual(self.engine.start_items(None), [])  # null = ready items only
        folder.mkdir()
        self.assertEqual(self.engine.start_items([item_id]), [item_id])  # retry
        queued = [p for p in self.rec.named("item") if p["id"] == item_id and p["status"] == "queued"][-1]
        self.assertIsNone(queued["error"])
        self.assertTrue(self.engine.wait_idle(30))
        first = self.engine.item(item_id)
        self.assertEqual(first["status"], "done", first["error"])
        self.assertEqual(first["output"]["path"], str(folder / "photo_forge.jpg"))
        self.assertEqual(self.engine.start_items([item_id]), [item_id])  # run again
        queued = [p for p in self.rec.named("item") if p["id"] == item_id and p["status"] == "queued"][-1]
        self.assertIsNone(queued["output"])
        self.assertTrue(self.engine.wait_idle(30))
        second = self.engine.item(item_id)
        self.assertEqual(second["output"]["path"], str(folder / "photo_forge (2).jpg"))
        self.assertTrue((folder / "photo_forge.jpg").is_file())  # the old output stays
        broken = self.add("notmedia.mp4")[0]
        self.assertEqual(self.engine.start_items([broken["id"]]), [])  # a probe failure cannot be retried

    def test_start_now_runs_and_runs_again(self):
        folder = self.out / "now"
        folder.mkdir(exist_ok=True)
        for old in folder.iterdir():
            old.unlink()
        self.engine.set_settings({"output_mode": "folder", "output_dir": str(folder)})
        item_id = self.add("photo.jpg")[0]["id"]
        self.assertTrue(self.engine.start_now(item_id))
        self.assertTrue(self.engine.wait_idle(30))
        self.assertEqual(self.engine.item(item_id)["output"]["path"], str(folder / "photo_forge.jpg"))
        self.assertTrue(self.engine.start_now(item_id))  # a done item runs again
        self.assertTrue(self.engine.wait_idle(30))
        self.assertEqual(self.engine.item(item_id)["output"]["path"], str(folder / "photo_forge (2).jpg"))
        statuses = [p["status"] for p in self.rec.named("item") if p["id"] == item_id]
        self.assertNotIn("queued", statuses)
        self.assertEqual(self.rec.named("batch_done")[-1], {"done": 1, "failed": 0, "cancelled": 0})
        broken = self.add("notmedia.mp4")[0]
        self.assertFalse(self.engine.start_now(broken["id"]))  # a probe failure cannot start
        self.assertFalse(self.engine.start_now("missing"))

    def test_missing_output_folder_at_job_time(self):
        folder = self.out / "vanishing"
        folder.mkdir(exist_ok=True)
        self.engine.set_settings({"output_mode": "folder", "output_dir": str(folder)})
        folder.rmdir()
        items = self.add("photo.jpg")
        self.engine.start_items(None)
        self.assertTrue(self.engine.wait_idle(30))
        self.assertEqual(self.engine.item(items[0]["id"])["error"], "The output folder does not exist.")

    def test_output_keeps_the_source_file_dates(self):
        """keep_file_dates (default on) gives the output its source's modified, accessed and creation times."""
        import os
        import shutil

        folder = self.out / "dated"
        folder.mkdir(exist_ok=True)
        for old in folder.iterdir():
            old.unlink()
        src = folder / "dated_photo.jpg"
        shutil.copyfile(media("photo.jpg"), src)
        modified = 1577934245_000000000  # 2020-01-02 03:04:05 UTC
        os.utime(src, ns=(modified + 10**9, modified))
        self.assertTrue(self.engine.settings.get()["keep_file_dates"])
        self.engine.set_settings({"output_dir": str(folder)})
        item_id = self.engine.add_files([str(src)])[0]["id"]
        self.assertTrue(self.engine.wait_probed(30))
        self.engine.start_items([item_id])
        self.assertTrue(self.engine.wait_idle(60))
        done = self.engine.item(item_id)
        self.assertEqual(done["status"], "done", done["error"])
        out_stat, src_stat = os.stat(done["output"]["path"]), os.stat(src)
        self.assertEqual(out_stat.st_mtime_ns, modified)
        if hasattr(src_stat, "st_birthtime_ns"):
            self.assertEqual(out_stat.st_birthtime_ns, src_stat.st_birthtime_ns)
        # off: the output carries the time it was written
        self.engine.set_settings({"keep_file_dates": False})
        self.engine.start_items([item_id])
        self.assertTrue(self.engine.wait_idle(60))
        again = self.engine.item(item_id)["output"]["path"]
        self.assertGreater(os.stat(again).st_mtime, time.time() - 120)

    def test_subfolders_of_a_dropped_folder_are_mirrored(self):
        import os
        import tempfile
        from pathlib import Path

        target = Path(tempfile.mkdtemp(prefix="mirror_", dir=self.out))
        self.engine.set_settings({"output_dir": str(target)})
        rels = ["", "2024", os.path.join("2024", "05")]
        items = self.engine.add_files([str(media("photo.jpg")), str(media("tone.mp3")), str(media("clip.mp4"))],
                                      rel_dirs=rels)
        self.assertTrue(self.engine.wait_probed(60))
        self.engine.set_item_settings(items[2]["id"], {"encoder": "cpu"})
        self.engine.start_items(None)
        self.assertTrue(self.engine.wait_idle(120))
        outputs = []
        for item in items:
            final = self.engine.item(item["id"])
            self.assertEqual(final["status"], "done", final["error"])
            outputs.append(Path(final["output"]["path"]))
        self.assertEqual([p.parent for p in outputs], [target / r if r else target for r in rels])
        with self.assertRaises(ValueError):
            self.engine.add_files(["a.mp4", "b.mp4"], rel_dirs=[""])

    def test_a_failed_job_removes_the_mirrored_folders_it_created(self):
        import os
        import tempfile
        from pathlib import Path

        target = Path(tempfile.mkdtemp(prefix="mirror_fail_", dir=self.out))
        (target / "kept").mkdir()  # a folder that was there before stays, even when empty
        self.engine.set_settings({"output_dir": str(target)})
        items = self.engine.add_files([str(media("noaudio.mkv")), str(media("small.mp4"))],
                                      rel_dirs=[os.path.join("2024", "05"), os.path.join("kept", "x")])
        self.assertTrue(self.engine.wait_probed(60))
        for item in items:
            self.engine.set_item_settings(item["id"], {"format": "mp3"})  # no audio track: the job fails
        self.engine.start_items(None)
        self.assertTrue(self.engine.wait_idle(60))
        self.assertEqual([self.engine.item(i["id"])["status"] for i in items], ["error", "error"])
        self.assertEqual(sorted(p.name for p in target.iterdir()), ["kept"])
        self.assertEqual(list((target / "kept").iterdir()), [])

    def _fail_holding_the_output(self, hold: float):
        """Run one audio job that writes its output, has it opened by another handle without delete sharing (as a
        virus scanner or the indexer does) for `hold` seconds, and fails. Returns (target folder, output path,
        seconds the job took, the holder's release event)."""
        import tempfile
        from pathlib import Path
        from unittest import mock

        target = Path(tempfile.mkdtemp(prefix="held_", dir=self.out))
        self.engine.set_settings({"output_dir": str(target)})
        items = self.engine.add_files([str(media("tone.mp3"))], rel_dirs=["sub"])
        self.assertTrue(self.engine.wait_probed(60))
        seen: list = []
        release = threading.Event()
        self.addCleanup(release.set)

        def hold_then_fail(src, info, settings, ffmpeg, out, ctx):
            out.write_bytes(b"\0" * 1000)
            holder = open(out, "rb")  # noqa: SIM115 - closed by the timer thread
            seen.append(out)

            def close_later():
                release.wait(hold)
                holder.close()

            threading.Thread(target=close_later, daemon=True).start()
            raise OSError(5, "the encoder lost its output")

        with mock.patch("media_forge.core.engine.audio_mod.run_audio", side_effect=hold_then_fail):
            started = time.monotonic()
            self.engine.start_items(None)
            self.assertTrue(self.engine.wait_idle(60))
            seconds = time.monotonic() - started
        self.assertEqual(self.engine.item(items[0]["id"])["status"], "error")
        return target, seen[0], seconds, release

    def test_a_failed_job_removes_an_output_another_process_holds_for_a_moment(self):
        target, out, seconds, _release = self._fail_holding_the_output(0.3)
        self.assertFalse(out.exists())
        self.assertEqual(list(target.iterdir()), [])  # the mirrored folder went too
        self.assertLess(seconds, 3.0)

    def test_a_failed_job_reports_an_output_it_cannot_remove_and_ends_in_bounded_time(self):
        with self.assertLogs("media_forge.core", "WARNING") as logs:
            target, out, seconds, release = self._fail_holding_the_output(30.0)
        self.assertTrue(out.exists())
        self.assertTrue(any("could not be removed" in line and out.name in line for line in logs.output),
                        logs.output)
        self.assertGreater(seconds, 2.5)
        self.assertLess(seconds, 6.0)
        release.set()


class QueueTest(unittest.TestCase):
    """The queue alone with a fake runner: order, lanes, parallelism, cancel of queued items."""

    def test_order_lanes_and_parallel(self):
        events = []
        running = set()
        peak = [0]
        lock = threading.Lock()

        def runner(item, ctx):
            with lock:
                running.add(item.id)
                peak[0] = max(peak[0], len(running))
            for i in range(10):
                ctx.report(progress=i / 10, stage="encoding")
                time.sleep(0.01)
            with lock:
                running.discard(item.id)
            return {"path": "x", "size": 1}

        q = JobQueue(lambda n, p: events.append((n, p)), runner, workers=2)
        q.start()
        for n in range(6):
            q.add(Item(id=f"i{n}", path=f"p{n}", name=f"n{n}", kind="image" if n % 2 else "video", size=1,
                       settings={}, status="ready"))
        self.assertEqual(q.start_items(None), [f"i{n}" for n in range(6)])
        self.assertTrue(q.wait_idle(10))
        self.assertLessEqual(peak[0], 3)  # parallel_jobs + the image lane
        self.assertGreaterEqual(peak[0], 2)
        self.assertTrue(all(i.status == "done" for i in q.items()))
        self.assertEqual([p for n, p in events if n == "batch_done"], [{"done": 6, "failed": 0, "cancelled": 0}])
        q.stop()

    def test_cancel_queued_and_failure(self):
        gate = threading.Event()

        def runner(item, ctx):
            if item.id == "a":
                gate.wait(5)
                raise RuntimeError("boom")
            return {}

        events = []
        q = JobQueue(lambda n, p: events.append((n, p)), runner, workers=1, image_lane=False)
        q.start()
        for name in ("a", "b"):
            q.add(Item(id=name, path=name, name=name, kind="video", size=1, settings={}, status="ready"))
        q.start_items(None)
        self.assertTrue(wait_for(lambda: q.get("a").status == "running", 5))
        self.assertTrue(q.cancel("b"))
        self.assertEqual(q.get("b").status, "ready")
        gate.set()
        self.assertTrue(q.wait_idle(5))
        self.assertEqual(q.get("a").status, "error")
        self.assertEqual(q.get("a").error, "The conversion failed; details are in the log.")
        self.assertEqual([p for n, p in events if n == "batch_done"][-1], {"done": 0, "failed": 1, "cancelled": 1})
        q.stop()

    def test_start_now_ignores_parallel_jobs(self):
        gates = {name: threading.Event() for name in ("a", "b", "c", "d")}

        def runner(item, ctx):
            if not gates[item.id].wait(5):
                raise RuntimeError("gate timeout")
            return {"path": item.id}

        events = []
        q = JobQueue(lambda n, p: events.append((n, p)), runner, workers=1, image_lane=False)
        q.start()
        for name in ("a", "b", "c"):
            q.add(Item(id=name, path=name, name=name, kind="video", size=1, settings={}, status="ready"))
        q.add(Item(id="d", path="d", name="d", kind="video", size=1, settings={}, status="probing"))
        q.add(Item(id="e", path="e", name="e", kind="video", size=1, settings={}, status="error",
                   error="Not a media file."))
        self.assertEqual(q.start_items(["a", "b", "c"]), ["a", "b", "c"])
        self.assertTrue(wait_for(lambda: q.get("a").status == "running", 5))
        self.assertTrue(q.start_now("c"))  # leaves the queue, runs beside a
        self.assertEqual(q.get("c").status, "running")
        self.assertEqual(q.get("b").status, "queued")
        self.assertFalse(q.start_now("a"))  # running
        self.assertFalse(q.start_now("c"))
        self.assertFalse(q.start_now("d"))  # probing
        self.assertFalse(q.start_now("e"))  # the probe failed
        self.assertFalse(q.start_now("zzz"))
        gates["c"].set()
        self.assertTrue(wait_for(lambda: q.get("c").status == "done", 5))
        self.assertEqual(q.get("a").status, "running")
        self.assertEqual(q.get("b").status, "queued")  # parallel_jobs = 1 still holds for the queue
        self.assertEqual([n for n, _ in events if n == "batch_done"], [])
        gates["a"].set()
        gates["b"].set()
        self.assertTrue(q.wait_idle(5))
        self.assertEqual([i.status for i in q.items()], ["done", "done", "done", "probing", "error"])
        self.assertEqual([p for n, p in events if n == "batch_done"], [{"done": 3, "failed": 0, "cancelled": 0}])
        c_statuses = [p["status"] for n, p in events if n == "item" and p["id"] == "c"]
        self.assertEqual(c_statuses[0], "queued")
        self.assertEqual(c_statuses[1], "running")
        self.assertEqual(c_statuses[-1], "done")
        gates["c"].clear()
        self.assertTrue(q.start_now("c"))  # a done item runs again, reset like start
        self.assertIsNone(q.get("c").output)
        gates["c"].set()
        self.assertTrue(q.wait_idle(5))
        self.assertEqual(q.get("c").output, {"path": "c"})
        self.assertTrue(wait_for(lambda: not any(t.is_alive() for t in q._extra), 5))  # the extra worker ends
        q.stop()

    def test_start_now_cancel_and_error_retry(self):
        gate = threading.Event()
        calls = []

        def runner(item, ctx):
            calls.append(item.id)
            if len(calls) == 1:
                raise UserError("Broken.")
            while not ctx.cancel.is_set() and not gate.is_set():
                time.sleep(0.01)
            if ctx.cancel.is_set():
                raise Cancelled()  # as the engine's runner does
            return {}

        events = []
        q = JobQueue(lambda n, p: events.append((n, p)), runner, workers=1, image_lane=False)
        q.start()
        q.add(Item(id="a", path="a", name="a", kind="video", size=1, settings={}, status="ready"))
        self.assertTrue(q.start_now("a"))
        self.assertTrue(q.wait_idle(5))
        self.assertEqual(q.get("a").status, "error")
        with q._lock:
            q._items["a"].info = object()  # a probed item that failed its job
        self.assertTrue(q.start_now("a"))  # retry
        self.assertIsNone(q.get("a").error)
        self.assertTrue(wait_for(lambda: len(calls) == 2, 5))
        self.assertTrue(q.cancel("a"))
        self.assertTrue(q.wait_idle(5))
        self.assertEqual(q.get("a").status, "ready")
        self.assertEqual([p for n, p in events if n == "batch_done"],
                         [{"done": 0, "failed": 1, "cancelled": 0}, {"done": 0, "failed": 0, "cancelled": 1}])
        q.stop()

    def test_parallel_lowered_then_raised_while_old_worker_busy(self):
        """3 -> 1 -> 2 while the third worker still runs its last job: two workers serve the queue afterwards."""
        gates = {name: threading.Event() for name in ("a", "b", "c")}
        running = set()
        peak = [0]
        lock = threading.Lock()

        def runner(item, ctx):
            with lock:
                running.add(item.id)
                peak[0] = max(peak[0], len(running))
            try:
                if item.id in gates:
                    if not gates[item.id].wait(5):
                        raise RuntimeError("gate timeout")
                else:
                    time.sleep(0.3)
            finally:
                with lock:
                    running.discard(item.id)
            return {}

        q = JobQueue(lambda n, p: None, runner, workers=3, image_lane=False)
        q.start()
        for name in ("a", "b", "c"):
            q.add(Item(id=name, path=name, name=name, kind="video", size=1, settings={}, status="ready"))
        q.start_items(None)
        self.assertTrue(wait_for(lambda: len(running) == 3, 5))
        q.set_parallel(1)
        gates["b"].set()  # the second worker ends its job and leaves (index 1 >= 1)
        self.assertTrue(wait_for(lambda: q.get("b").status == "done", 5))
        time.sleep(0.2)
        q.set_parallel(2)  # the third worker is still busy with c and will leave after it
        gates["c"].set()
        self.assertTrue(wait_for(lambda: q.get("c").status == "done", 5))
        time.sleep(0.2)
        with lock:
            peak[0] = 0
        for n in range(4):
            q.add(Item(id=f"x{n}", path=f"x{n}", name=f"x{n}", kind="video", size=1, settings={}, status="ready"))
        q.start_items([f"x{n}" for n in range(4)])
        gates["a"].set()
        self.assertTrue(q.wait_idle(10))
        self.assertEqual(peak[0], 2)  # parallel_jobs = 2 holds
        q.stop()

    def test_cancel_after_the_runner_returned_keeps_the_output(self):
        """A cancel that arrives once the runner has returned its output leaves the item done (the file exists)."""
        def runner(item, ctx):
            ctx.cancel.set()  # the cancel lands after the runner's last cancel point
            return {"path": "out.mp4", "size": 1}

        events = []
        q = JobQueue(lambda n, p: events.append((n, p)), runner, workers=1, image_lane=False)
        q.start()
        q.add(Item(id="a", path="a", name="a", kind="video", size=1, settings={}, status="ready"))
        q.start_items(None)
        self.assertTrue(q.wait_idle(5))
        self.assertEqual(q.get("a").status, "done")
        self.assertEqual(q.get("a").output, {"path": "out.mp4", "size": 1})
        self.assertEqual([p for n, p in events if n == "batch_done"], [{"done": 1, "failed": 0, "cancelled": 0}])
        q.stop()


if __name__ == "__main__":
    unittest.main()
