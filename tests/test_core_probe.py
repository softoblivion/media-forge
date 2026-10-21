"""probe: kind detection, metadata of each kind, thumbnails."""

from __future__ import annotations

import base64
import io
import unittest

from PIL import Image

from media_forge.core import probe
from media_forge.core.errors import UserError

from tests.core_support import FFMPEG, FFPROBE, ScratchCase, media, needs_ffmpeg


def _thumb_image(url: str) -> Image.Image:
    header, data = url.split(",", 1)
    return Image.open(io.BytesIO(base64.b64decode(data)))


@needs_ffmpeg
class ProbeTest(ScratchCase):
    def test_video(self):
        info = probe.probe(media("clip.mp4"), FFPROBE)
        self.assertEqual(info.kind, "video")
        pub = info.public()
        self.assertEqual(set(pub), set(probe.ProbeInfo.PUBLIC))
        self.assertEqual((pub["width"], pub["height"]), (320, 240))
        self.assertAlmostEqual(pub["fps"], 30.0)
        self.assertAlmostEqual(pub["duration"], 2.0, delta=0.1)
        self.assertEqual((pub["vcodec"], pub["acodec"]), ("h264", "aac"))
        self.assertTrue(pub["has_audio"])
        self.assertEqual(pub["format"], "mov,mp4")
        self.assertGreater(pub["bitrate"], 0)
        self.assertEqual(pub["sample_rate"], 44100)
        thumb = probe.thumbnail(media("clip.mp4"), "video", info, FFMPEG)
        self.assertTrue(thumb.startswith("data:image/jpeg;base64,"))
        img = _thumb_image(thumb)
        self.assertEqual(max(img.size), 240)

    def test_rotation_swaps_dimensions(self):
        info = probe.probe(media("rotated.mp4"), FFPROBE)
        self.assertIn(info.rotation, (90, 270))
        self.assertEqual((info.width, info.height), (240, 320))

    def test_audio_and_audio_only_container(self):
        info = probe.probe(media("tone.mp3"), FFPROBE)
        self.assertEqual(info.kind, "audio")
        self.assertIsNone(info.width)
        self.assertEqual(info.acodec, "mp3")
        thumb = probe.thumbnail(media("tone.mp3"), "audio", info, FFMPEG)
        self.assertTrue(thumb.startswith("data:image/png;base64,"))
        self.assertEqual(max(_thumb_image(thumb).size), 240)
        only = probe.probe(media("audio_only.mp4"), FFPROBE)
        self.assertEqual(only.kind, "audio")

    def test_images(self):
        info = probe.probe(media("photo.jpg"), FFPROBE)
        self.assertEqual((info.kind, info.format, info.width, info.height), ("image", "JPEG", 400, 300))
        self.assertIsNone(info.duration)
        thumb = probe.thumbnail(media("photo.jpg"), "image", info, FFMPEG)
        self.assertEqual(_thumb_image(thumb).size, (240, 180))
        rotated = probe.probe(media("photo_exif.jpg"), FFPROBE)
        self.assertEqual((rotated.width, rotated.height), (300, 400))
        alpha = probe.probe(media("alpha.png"), FFPROBE)
        self.assertTrue(alpha.has_alpha)
        self.assertTrue(probe.thumbnail(media("alpha.png"), "image", alpha, FFMPEG).startswith("data:image/png"))
        gray = probe.probe(media("gray16.png"), FFPROBE)
        self.assertEqual(gray.format, "PNG")
        self.assertIsNotNone(probe.thumbnail(media("gray16.png"), "image", gray, FFMPEG))

    def test_gif_kind_by_frames(self):
        self.assertEqual(probe.probe(media("single.gif"), FFPROBE).kind, "image")
        anim = probe.probe(media("anim.gif"), FFPROBE)
        self.assertEqual(anim.kind, "video")
        self.assertEqual(anim.format, "gif")
        self.assertEqual((anim.width, anim.height), (160, 120))

    def test_errors(self):
        with self.assertRaises(UserError) as cm:
            probe.probe(media("notmedia.mp4"), FFPROBE)
        self.assertEqual(cm.exception.message, "Not a media file.")
        with self.assertRaises(UserError) as cm:
            probe.probe(self.out / "missing.mp4", FFPROBE)
        self.assertEqual(cm.exception.message, "The file was not found.")
        fake = self.out / "fake.jpg"
        fake.write_bytes(b"not a jpeg")
        with self.assertRaises(UserError):
            probe.probe(fake, FFPROBE)

    def test_image_without_ffprobe(self):
        info = probe.probe(media("photo.png"), None)
        self.assertEqual(info.kind, "image")


if __name__ == "__main__":
    unittest.main()
