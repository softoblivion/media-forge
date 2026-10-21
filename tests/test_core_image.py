"""image: formats, metadata stripping, size cap, downscale, Lanczos and FSRCNN upscale (deterministic)."""

from __future__ import annotations

import hashlib
import unittest

from PIL import Image

from media_forge.core import catalog, image, probe
from media_forge.core.errors import UserError
from media_forge.core.paths import AppPaths
from media_forge.core.upscale import Model

from tests.core_support import FakeCtx, ScratchCase, caps, media


def sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ImageTest(ScratchCase):
    def run_job(self, src: str, out_name: str, **settings):
        info = probe.probe(media(src), None)
        s = catalog.normalize("image", settings, caps())
        out = self.out_path(out_name)
        ctx = FakeCtx()
        paths = AppPaths(home=self.paths.home)
        result = image.run_image(media(src), info, s, out, ctx,
                                 get_model=lambda name, scale: Model.load(name, scale, paths))
        self.assertTrue(out.is_file() and out.stat().st_size > 0)
        return out, result, ctx

    def assert_no_metadata(self, out) -> None:
        with Image.open(out) as img:
            info = img.info
            self.assertFalse(info.get("exif"), out.name)
            self.assertFalse(info.get("xmp") or info.get("XML:com.adobe.xmp"), out.name)
            self.assertNotIn("comment", info)
            self.assertNotIn("Comment", info)
            exif = img.getexif()
            for tag in (0x010F, 0x0131, 0x0112):  # Make, Software, Orientation (TIFF keeps its own image tags)
                self.assertNotIn(tag, exif, out.name)
        raw = out.read_bytes()
        for needle in (b"TestCam", b"TestSoftware", b"secret comment", b"png text chunk", b"xmpmeta"):
            self.assertNotIn(needle, raw, out.name)

    def test_formats(self):
        expected = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP", "tiff": "TIFF", "bmp": "BMP"}
        if caps()["avif"]:
            expected["avif"] = "AVIF"
        if caps()["heif"]:
            expected["heic"] = "HEIF"
        for fmt, pil_name in expected.items():
            with self.subTest(fmt=fmt):
                out, result, ctx = self.run_job("photo_exif.jpg", f"conv.{fmt}", format=fmt, quality=80)
                with Image.open(out) as img:
                    self.assertEqual(img.format, pil_name)
                    self.assertEqual(img.size, (300, 400))  # EXIF orientation applied
                self.assertEqual(result, {"width": 300, "height": 400})
                self.assert_no_metadata(out)
                self.assertEqual(ctx.stages[0], "analyzing")
                self.assertEqual(ctx.stages[-1], "writing")

    def test_png_text_chunks_dropped(self):
        out, _, _ = self.run_job("photo.png", "clean.png", format="png")
        self.assert_no_metadata(out)

    def test_metadata_kept_when_strip_off(self):
        out, _, _ = self.run_job("photo_exif.jpg", "keep.jpg", format="jpg", strip_metadata=False)
        with Image.open(out) as img:
            exif = img.getexif()
            self.assertEqual(exif.get(0x010F), "TestCam")
            self.assertIn(exif.get(0x0112, 1), (1, None))  # orientation was applied to the pixels

    def test_icc_profile_kept(self):
        from PIL import ImageCms
        src = self.out / "icc.png"
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        Image.new("RGB", (64, 48), (10, 120, 200)).save(src, icc_profile=profile)
        info = probe.probe(src, None)
        out = self.out_path("icc.webp")
        image.run_image(src, info, catalog.normalize("image", {"format": "webp"}), out, FakeCtx())
        with Image.open(out) as img:
            self.assertEqual(img.info.get("icc_profile"), profile)

    def test_alpha(self):
        out, _, _ = self.run_job("alpha.png", "alpha.webp", format="webp")
        with Image.open(out) as img:
            self.assertEqual(img.mode, "RGBA")
        out, _, _ = self.run_job("alpha.png", "alpha.jpg", format="jpg")
        with Image.open(out) as img:
            self.assertEqual(img.mode, "RGB")
            self.assertEqual(img.getpixel((2, 2)), (255, 255, 255))  # composited on white

    def test_modes(self):
        out, _, _ = self.run_job("gray16.png", "gray16_out.png", format="png")
        with Image.open(out) as img:
            self.assertTrue(img.mode.startswith("I"))
        out, _, _ = self.run_job("gray16.png", "gray16_out.jpg", format="jpg")
        with Image.open(out) as img:
            self.assertEqual(img.mode, "L")
        out, _, _ = self.run_job("cmyk.jpg", "cmyk_out.png", format="png")
        with Image.open(out) as img:
            self.assertEqual(img.mode, "RGB")
        out, _, _ = self.run_job("single.gif", "gif_out.webp", format="webp")
        with Image.open(out) as img:
            self.assertEqual(img.size, (400, 300))

    def test_downscale(self):
        out, result, _ = self.run_job("photo.jpg", "half.jpg", scale="0.5x")
        self.assertEqual(result, {"width": 200, "height": 150})
        out, result, _ = self.run_job("photo.jpg", "quarter.png", format="png", scale="0.25x")
        self.assertEqual(result, {"width": 100, "height": 75})

    def test_size_limit_quality_search(self):
        cap_mb = 0.25
        out, result, _ = self.run_job("big.png", "capped.jpg", format="jpg", quality=95, size_limit_mb=cap_mb)
        self.assertLessEqual(out.stat().st_size, cap_mb * 1024 * 1024)
        self.assertEqual(result, {"width": 1600, "height": 1200})  # met by the quality search alone
        out, result, _ = self.run_job("big.png", "capped_small.webp", format="webp", size_limit_mb=0.05)
        self.assertLessEqual(out.stat().st_size, 0.05 * 1024 * 1024)
        self.assertLess(result["width"], 1600)  # needed the downscale ladder

    def test_size_limit_errors(self):
        with self.assertRaises(UserError) as cm:
            self.run_job("big.png", "capped.png", format="png", size_limit_mb=0.1)
        self.assertEqual(cm.exception.message, "This format cannot be limited in size.")
        out, _, _ = self.run_job("photo.png", "fits.png", format="png", size_limit_mb=5)  # lossless that fits
        self.assertLess(out.stat().st_size, 5 * 1024 * 1024)
        with self.assertRaises(UserError) as cm:
            self.run_job("big.png", "tiny.jpg", format="jpg", size_limit_mb=0.0001)
        self.assertEqual(cm.exception.message, "The size limit is too small for this image.")

    def test_lanczos_upscale(self):
        for factor in (2, 4):
            with self.subTest(factor=factor):
                out, result, ctx = self.run_job("photo.jpg", f"lanczos{factor}x.png", format="png",
                                                scale=f"{factor}x")
                self.assertEqual(result, {"width": 400 * factor, "height": 300 * factor})
                self.assertIn("upscaling", ctx.stages)
                again, _, _ = self.run_job("photo.jpg", f"lanczos{factor}x_again.png", format="png",
                                           scale=f"{factor}x")
                self.assertEqual(sha(out), sha(again))

    def test_fsrcnn_upscale_deterministic(self):
        for factor in (2, 4):
            with self.subTest(factor=factor):
                out, result, _ = self.run_job("photo.jpg", f"fsrcnn{factor}x.png", format="png", scale=f"{factor}x",
                                              upscale_method="fsrcnn")
                self.assertEqual(result, {"width": 400 * factor, "height": 300 * factor})
                again, _, _ = self.run_job("photo.jpg", f"fsrcnn{factor}x_again.png", format="png",
                                           scale=f"{factor}x", upscale_method="fsrcnn")
                self.assertEqual(sha(out), sha(again))
        out, _, _ = self.run_job("alpha.png", "fsrcnn_alpha.png", format="png", scale="2x", upscale_method="fsrcnn")
        with Image.open(out) as img:
            self.assertEqual((img.mode, img.size), ("RGBA", (800, 600)))
        out, _, _ = self.run_job("photo.jpg", "fsrcnn.jpg", format="jpg", scale="2x", upscale_method="fsrcnn")
        again, _, _ = self.run_job("photo.jpg", "fsrcnn_again.jpg", format="jpg", scale="2x", upscale_method="fsrcnn")
        self.assertEqual(sha(out), sha(again))

    def test_cancel(self):
        info = probe.probe(media("photo.jpg"), None)
        ctx = FakeCtx()
        ctx.cancel.set()
        from media_forge.core.errors import Cancelled
        out = self.out_path("cancelled.jpg")
        with self.assertRaises(Cancelled):
            image.run_image(media("photo.jpg"), info, catalog.normalize("image", {}), out, ctx)
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
