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
        self.assertEqual(s.patch({"update_check_interval": "2h"})["update_check_interval"], "30m")
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

    def test_memory_only(self):
        paths = AppPaths(home=self.out / "memory")
        s = Settings(paths, persist=False)
        s.load()
        s.patch({"parallel_jobs": 2})
        self.assertFalse(paths.settings_file.exists())


if __name__ == "__main__":
    unittest.main()
