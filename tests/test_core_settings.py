"""settings: defaults, validation, persistence, broken file recovery."""

from __future__ import annotations

import json
import unittest

from media_forge.core import catalog
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
