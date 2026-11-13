"""settings: defaults, validation, persistence, broken file recovery."""

from __future__ import annotations

import json
import threading
import time
import unittest
from unittest import mock

from media_forge.core import catalog
from media_forge.core import settings as settings_mod
from media_forge.core.errors import UserError
from media_forge.core.paths import AppPaths
from media_forge.core.settings import Settings, default_settings

from tests.core_support import ScratchCase


class SettingsTest(ScratchCase):
    def fresh(self, name: str) -> Settings:
        paths = AppPaths(home=self.out / name)
        paths.ensure()
        if paths.settings_file.exists():
            paths.settings_file.unlink()
        return Settings(paths)

    def test_concurrent_saves_keep_the_newest_values(self):
        """A save that writes slowly cannot overwrite a newer patch saved meanwhile."""
        s = self.fresh("save_race")
        s.load()
        first_started, release = threading.Event(), threading.Event()
        calls = []
        real_write = settings_mod.atomic_write_json

        def slow_write(path, data):
            calls.append(data["parallel_jobs"])
            if len(calls) == 1:
                first_started.set()
                release.wait(5)
            real_write(path, data)

        with mock.patch.object(settings_mod, "atomic_write_json", slow_write):
            a = threading.Thread(target=s.patch, args=({"parallel_jobs": 2},))
            a.start()
            self.assertTrue(first_started.wait(5))
            b = threading.Thread(target=s.patch, args=({"parallel_jobs": 3},))
            b.start()
            time.sleep(0.3)
            release.set()
            a.join(5)
            b.join(5)
        raw = json.loads(s.paths.settings_file.read_text(encoding="utf-8"))
        self.assertEqual(raw["parallel_jobs"], 3)
        self.assertEqual(Settings(s.paths).load()["parallel_jobs"], 3)

    def test_defaults(self):
        s = self.fresh("defaults")
        data = s.load()
        self.assertEqual(data, default_settings())
        self.assertEqual(data["suffix"], "_forge")
        self.assertEqual(data["defaults"]["video"], catalog.DEFAULTS["video"])

    def test_persistence_roundtrip(self):
        s = self.fresh("roundtrip")
        s.load()
        s.patch({"parallel_jobs": 3, "suffix": "_small", "defaults": {"image": {"format": "webp", "quality": 60}}})
        again = Settings(s.paths).load()
        self.assertEqual(again["parallel_jobs"], 3)
        self.assertEqual(again["suffix"], "_small")
        self.assertEqual(again["defaults"]["image"]["format"], "webp")
        self.assertEqual(again["defaults"]["image"]["quality"], 60)
        raw = json.loads(s.paths.settings_file.read_text(encoding="utf-8"))
        self.assertEqual(raw["version"], 1)

    def test_validation(self):
        s = self.fresh("validation")
        s.load()
        self.assertEqual(s.patch({"parallel_jobs": 9})["parallel_jobs"], 3)
        self.assertNotIn("update_check_interval", s.patch({"update_check_interval": "2h"}))  # the setting is gone
        with self.assertRaises(UserError) as cm:
            s.patch({"suffix": "a/b"})
        self.assertIn("not allowed", cm.exception.message)
        with self.assertRaises(UserError):
            s.patch({"suffix": "x" * 25})
        with self.assertRaises(UserError) as cm:
            s.patch({"suffix": ""})
        self.assertEqual(cm.exception.message, "A suffix is needed when files are written next to the source.")
        with self.assertRaises(UserError) as cm:
            s.patch({"output_mode": "folder", "output_dir": str(self.out / "missing")})
        self.assertEqual(cm.exception.message, "The output folder does not exist.")
        self.assertEqual(s.get()["output_mode"], "beside")  # a refused patch changes nothing
        result = s.patch({"output_mode": "folder", "output_dir": str(self.out), "suffix": ""})
        self.assertEqual((result["output_mode"], result["suffix"]), ("folder", ""))
        result = s.patch({"defaults": {"video": {"format": "webm", "codec": "h264", "junk": 1}}})
        self.assertEqual(result["defaults"]["video"]["codec"], "vp9")
        self.assertNotIn("junk", result["defaults"]["video"])
        self.assertNotIn("junk", s.patch({"junk": 1}))

    def test_broken_file(self):
        s = self.fresh("broken")
        s.paths.settings_file.write_text("{not json", encoding="utf-8")
        data = s.load()
        self.assertEqual(data, default_settings())
        self.assertTrue(s.paths.settings_file.with_name("settings.json.bad").exists())
        self.assertTrue(s.paths.settings_file.exists())

    def test_bad_values_repaired_per_key(self):
        s = self.fresh("repair")
        s.paths.settings_file.write_text(json.dumps({
            "parallel_jobs": "two", "suffix": "bad|name", "output_mode": "folder", "output_dir": "Z:\\nowhere",
            "defaults": {"audio": {"format": "opus", "bitrate_k": 320}}}), encoding="utf-8")
        data = s.load()
        self.assertEqual(data["parallel_jobs"], 1)
        self.assertEqual(data["suffix"], "_forge")
        self.assertEqual(data["output_mode"], "folder")  # kept; jobs report the missing folder
        self.assertEqual(data["defaults"]["audio"]["bitrate_k"], 256)

    def test_auto_download_defaults_to_on(self):
        s = self.fresh("auto_download")
        self.assertIs(s.load()["auto_download_updates"], True)
        s.paths.settings_file.write_text(json.dumps({"suffix": "_x"}), encoding="utf-8")  # key absent from the file
        self.assertIs(s.load()["auto_download_updates"], True)
        s.paths.settings_file.write_text(json.dumps({"auto_download_updates": False}), encoding="utf-8")
        self.assertIs(s.load()["auto_download_updates"], False)  # a copy that turned it off keeps it off

    def test_keep_file_dates_defaults_to_on(self):
        s = self.fresh("keep_dates")
        self.assertIs(s.load()["keep_file_dates"], True)
        s.paths.settings_file.write_text(json.dumps({"suffix": "_x"}), encoding="utf-8")  # a file of an older version
        self.assertIs(s.load()["keep_file_dates"], True)
        self.assertIs(s.patch({"keep_file_dates": False})["keep_file_dates"], False)
        self.assertIs(s.load()["keep_file_dates"], False)  # survives a restart
        self.assertIs(s.patch({"keep_file_dates": "yes"})["keep_file_dates"], False)  # a non-bool keeps the value

    def test_skipped_major_of_older_versions_is_dropped(self):
        s = self.fresh("old_skipped_major")
        text = json.dumps({"version": 1, "skipped_major": 3, "auto_download_updates": True, "suffix": "_x"})
        s.paths.settings_file.write_text(text, encoding="utf-8")
        data = s.load()
        self.assertNotIn("skipped_major", data)
        self.assertEqual((data["suffix"], data["auto_download_updates"]), ("_x", True))
        self.assertFalse(s.paths.settings_file.with_name("settings.json.bad").exists())
        self.assertNotIn("skipped_major", s.patch({"skipped_major": 4, "parallel_jobs": 2}))
        stored = json.loads(s.paths.settings_file.read_text(encoding="utf-8"))
        self.assertNotIn("skipped_major", stored)  # not written back
        self.assertEqual(stored["parallel_jobs"], 2)

    def test_interval_key_of_older_versions_is_dropped(self):
        s = self.fresh("old_interval")
        text = json.dumps({"version": 1, "update_check_interval": "6h", "suffix": "_x"})
        s.paths.settings_file.write_text(text, encoding="utf-8")
        data = s.load()
        self.assertNotIn("update_check_interval", data)
        self.assertEqual(data["suffix"], "_x")
        self.assertEqual(s.paths.settings_file.read_text(encoding="utf-8"), text)  # loading does not rewrite the file
        self.assertFalse(s.paths.settings_file.with_name("settings.json.bad").exists())
        s.patch({"parallel_jobs": 2})  # the next save writes the file without the key
        raw = json.loads(s.paths.settings_file.read_text(encoding="utf-8"))
        self.assertNotIn("update_check_interval", raw)

    def test_ui_scale(self):
        s = self.fresh("scale")
        self.assertEqual(s.load()["ui_scale"], 100)
        self.assertEqual(s.patch({"ui_scale": 150})["ui_scale"], 150)
        self.assertEqual(Settings(s.paths).load()["ui_scale"], 150)
        self.assertEqual(s.patch({"ui_scale": "175"})["ui_scale"], 175)
        self.assertEqual(s.patch({"ui_scale": 140})["ui_scale"], 150)   # nearest step
        self.assertEqual(s.patch({"ui_scale": 112.5})["ui_scale"], 125)  # the larger step on a tie
        self.assertEqual(s.patch({"ui_scale": 900})["ui_scale"], 200)
        for junk in ("big", None, True, -5, 0):
            with self.subTest(junk=junk):
                self.assertEqual(s.patch({"ui_scale": junk})["ui_scale"], 100)
        s.paths.settings_file.write_text(json.dumps({"ui_scale": 160}), encoding="utf-8")
        self.assertEqual(s.load()["ui_scale"], 150)

    def test_window_bounds(self):
        s = self.fresh("bounds")
        self.assertIsNone(s.load()["window_bounds"])  # absent from older files: the default size, centred
        bounds = {"x": -1500, "y": 40, "width": 700, "height": 900}
        self.assertEqual(s.patch({"window_bounds": bounds})["window_bounds"], bounds)
        self.assertEqual(Settings(s.paths).load()["window_bounds"], bounds)  # survives a restart
        self.assertEqual(s.patch({"window_bounds": {"x": 1.0, "y": 2.0, "width": 630.0, "height": 840.0}})
                         ["window_bounds"], {"x": 1, "y": 2, "width": 630, "height": 840})
        junk_values = ("big", 5, [1, 2, 3, 4], {"x": 1, "y": 2, "width": 630}, {"x": True, "y": 2, "width": 3,
                       "height": 4}, {"x": 1, "y": 2, "width": 0, "height": 840},
                       {"x": 1, "y": 2, "width": 630.5, "height": 840}, {"x": 10**7, "y": 2, "width": 6, "height": 8})
        for junk in junk_values:
            with self.subTest(junk=junk):
                self.assertIsNone(s.patch({"window_bounds": junk})["window_bounds"])
        s.paths.settings_file.write_text(json.dumps({"window_bounds": "junk", "suffix": "_y"}), encoding="utf-8")
        data = s.load()
        self.assertIsNone(data["window_bounds"])
        self.assertEqual(data["suffix"], "_y")  # a bad value never discards the other keys

    def test_theme(self):
        s = self.fresh("theme")
        self.assertEqual(s.load()["theme"], "nova")
        self.assertEqual(s.patch({"theme": "frost"})["theme"], "frost")
        self.assertEqual(Settings(s.paths).load()["theme"], "frost")
        self.assertEqual(s.patch({"theme": "neon"})["theme"], "nova")  # unknown names fall back to the default
        self.assertEqual(s.patch({"theme": 7})["theme"], "nova")
        s.paths.settings_file.write_text(json.dumps({"theme": "bloom"}), encoding="utf-8")
        self.assertEqual(s.load()["theme"], "bloom")
        s.paths.settings_file.write_text(json.dumps({"theme": "sunset"}), encoding="utf-8")
        self.assertEqual(s.load()["theme"], "nova")

    def test_memory_only(self):
        paths = AppPaths(home=self.out / "memory")
        s = Settings(paths, persist=False)
        s.load()
        s.patch({"parallel_jobs": 2})
        self.assertFalse(paths.settings_file.exists())


if __name__ == "__main__":
    unittest.main()
