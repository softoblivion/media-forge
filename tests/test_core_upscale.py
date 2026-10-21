"""upscale: model files and hashes, Lanczos, sharpen, tiles, EDSR download path (served locally)."""

from __future__ import annotations

import hashlib
import unittest
from unittest import mock

import numpy as np

from media_forge.core import upscale
from media_forge.core.errors import UserError
from media_forge.core.paths import AppPaths, bundled_models_dir

from tests.core_support import ScratchCase
from tests.test_core_tools import LocalServer


class ModelFilesTest(unittest.TestCase):
    def test_bundled_fsrcnn_hashes(self):
        for scale in (2, 4):
            entry = upscale.MODEL_FILES[("fsrcnn", scale)]
            path = bundled_models_dir() / entry.filename
            self.assertTrue(path.is_file(), path)
            self.assertEqual(path.stat().st_size, entry.size)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry.sha256)

    def test_models_md_lists_every_hash(self):
        text = (bundled_models_dir() / "MODELS.md").read_text(encoding="utf-8")
        for entry in upscale.MODEL_FILES.values():
            self.assertIn(entry.sha256, text)
            self.assertIn(entry.filename, text)


class ArrayTest(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(3)
        self.img = rng.integers(0, 255, (60, 80, 3), dtype=np.uint8)

    def test_lanczos_and_sharpen(self):
        up = upscale.lanczos(self.img, 2)
        self.assertEqual(up.shape, (120, 160, 3))
        self.assertTrue(np.array_equal(upscale.sharpen(up, 0), up))
        sharp = upscale.sharpen(up, 0.5)
        self.assertEqual(sharp.shape, up.shape)
        self.assertFalse(np.array_equal(sharp, up))
        gray = upscale.lanczos(self.img[:, :, :1], 4)
        self.assertEqual(gray.shape, (240, 320, 1))

    def test_upscale_array_channels(self):
        paths = AppPaths()
        model = upscale.Model.load("fsrcnn", 2, paths)
        rgba = np.dstack([self.img, np.full((60, 80), 128, np.uint8)])
        for arr, channels in ((self.img, 3), (rgba, 4), (self.img[:, :, 0], 1)):
            out = upscale.upscale_array(arr, 2, "fsrcnn", True, model)
            self.assertEqual(out.shape[:2], (120, 160))
            self.assertEqual(out.shape[2], channels)
        alpha_out = upscale.upscale_array(rgba, 2, "fsrcnn", True, model)[:, :, 3]
        self.assertTrue(np.all(alpha_out == 128))

    def test_tiles_match_whole_frame_closely(self):
        paths = AppPaths()
        model = upscale.Model.load("fsrcnn", 2, paths)
        rng = np.random.default_rng(5)
        frame = rng.integers(0, 255, (300, 420, 3), dtype=np.uint8)
        whole = model.upsample(frame)
        model.tile, model.overlap = 128, 16
        tiled = model.upsample(frame)
        self.assertEqual(whole.shape, tiled.shape)
        diff = np.abs(whole.astype(int) - tiled.astype(int))
        self.assertLessEqual(diff.mean(), 0.5)
        again = model.upsample(frame)
        self.assertTrue(np.array_equal(tiled, again))


class DownloadTest(ScratchCase):
    def test_edsr_download_verify_and_damage(self):
        payload = b"stand-in model bytes " * 100
        digest = hashlib.sha256(payload).hexdigest()
        served = self.out / "served"
        served.mkdir(exist_ok=True)
        (served / "EDSR_x2.pb").write_bytes(payload)
        fake = upscale.ModelFile("edsr", 2, "EDSR_x2.pb", digest, len(payload), False)
        paths = AppPaths(home=self.out / "dl_home")
        paths.ensure()
        with LocalServer(served) as server, \
                mock.patch.dict(upscale.MODEL_FILES, {("edsr", 2): fake}), \
                mock.patch.dict(upscale.MODEL_BASE_URLS, {"edsr": server.url}):
            self.assertEqual(upscale.model_status(paths)["edsr"], "missing")
            progress = []
            path = upscale.ensure_model("edsr", 2, paths, on_progress=lambda d, t: progress.append((d, t)))
            self.assertEqual(path, paths.models_dir / "EDSR_x2.pb")
            self.assertEqual(path.read_bytes(), payload)
            self.assertTrue(progress)
            self.assertIn(digest, paths.models_file.read_text(encoding="utf-8"))
            self.assertFalse(list(paths.models_dir.glob("*.part")))
            # a damaged file on disk is detected on load and deleted
            path.write_bytes(b"x" * len(payload))
            upscale._verified.clear()
            with self.assertRaises(UserError) as cm:
                upscale.verify_model("edsr", 2, paths)
            self.assertEqual(cm.exception.message, "The model file is damaged.")
            self.assertFalse(path.exists())
            # a download with the wrong content is refused
            (served / "EDSR_x2.pb").write_bytes(b"other bytes")
            with self.assertRaises(UserError) as cm:
                upscale.ensure_model("edsr", 2, paths)
            self.assertEqual(cm.exception.message, "The model file is damaged.")
            self.assertFalse(path.exists())

    def test_download_failure_message(self):
        paths = AppPaths(home=self.out / "dl_fail")
        paths.ensure()
        with mock.patch.dict(upscale.MODEL_BASE_URLS, {"edsr": "http://127.0.0.1:9/"}):
            with self.assertRaises(UserError) as cm:
                upscale.ensure_model("edsr", 4, paths)
        self.assertEqual(cm.exception.message, "The model could not be downloaded.")

    def test_real_edsr_when_present(self):
        paths = AppPaths()
        if not upscale.model_present("edsr", 2, paths):
            self.skipTest("EDSR_x2.pb is not in the data folder (downloaded on first use)")
        model = upscale.Model.load("edsr", 2, paths)
        frame = np.random.default_rng(1).integers(0, 255, (40, 60, 3), dtype=np.uint8)
        out = model.upsample(frame)
        self.assertEqual(out.shape, (80, 120, 3))
        self.assertTrue(np.array_equal(out, model.upsample(frame)))


if __name__ == "__main__":
    unittest.main()
