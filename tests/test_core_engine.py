"""engine and jobs: add / probe / settings / start / cancel / remove / clear, events, throttling, batch_done."""

from __future__ import annotations

import threading
import time
import unittest

from media_forge.core import catalog
from media_forge.core.engine import Engine
from media_forge.core.errors import UserError
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

    def test_missing_output_folder_at_job_time(self):
        folder = self.out / "vanishing"
        folder.mkdir(exist_ok=True)
        self.engine.set_settings({"output_mode": "folder", "output_dir": str(folder)})
        folder.rmdir()
        items = self.add("photo.jpg")
        self.engine.start_items(None)
        self.assertTrue(self.engine.wait_idle(30))
        self.assertEqual(self.engine.item(items[0]["id"])["error"], "The output folder does not exist.")


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


if __name__ == "__main__":
    unittest.main()
