"""probe: kind detection, metadata of each kind, thumbnails."""

from __future__ import annotations

import base64
import io
import json
import subprocess
import unittest
from fractions import Fraction
from pathlib import Path
from unittest import mock

from PIL import GifImagePlugin, Image

from media_forge.core import catalog, probe, video
from media_forge.core.errors import UserError

from tests.core_support import FFMPEG, FFPROBE, FakeCtx, ScratchCase, caps, media, needs_ffmpeg


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

    def test_animated_webp_and_apng_are_videos(self):
        for name, fmt, alpha in (("anim.webp", "webp", None), ("anim.png", "apng", True)):
            with self.subTest(name=name):
                info = probe.probe(media(name), FFPROBE)
                self.assertEqual((info.kind, info.format, info.width, info.height), ("video", fmt, 160, 120))
                self.assertAlmostEqual(info.duration, 1.2, delta=0.01)  # from the frame durations
                self.assertEqual(info.total_frames, 12)
                if alpha is not None:  # Pillow and ffmpeg read every animated WebP as RGBA
                    self.assertEqual(info.has_alpha, alpha)
                self.assertIsNotNone(info.bitrate)
                self.assertTrue(probe.thumbnail(media(name), "video", info, FFMPEG).startswith("data:image/"))
        self.assertEqual(probe.probe(media("photo.webp"), FFPROBE).kind, "image")  # a still stays an image
        with self.assertRaises(UserError) as cm:  # an animation needs ffprobe
            probe.probe(media("anim.webp"), None)
        self.assertEqual(cm.exception.message, "ffmpeg is not available.")

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


def decoded_durations(path) -> list[Fraction]:
    """Frame durations as Pillow gives them after decoding each frame, made exact (an APNG delay has a denominator
    of at most 65535)."""
    with Image.open(path) as img:
        out = []
        for index in range(img.n_frames):
            img.seek(index)
            img.load()
            ms = img.info.get("duration") or 0
            out.append(Fraction(ms) if isinstance(ms, int) else Fraction(ms / 1000).limit_denominator(65535) * 1000)
        return out


@needs_ffmpeg
class AnimationTimingTest(ScratchCase):
    def test_frame_rate_and_duration_are_exact(self):
        cases = (  # file, frames, rate, duration (s), last frame (s)
            ("anim30.png", 60, "30", 2.0, 1 / 30),
            ("anim24.png", 48, "24", 2.0, 1 / 24),
            ("anim_steps.webp", 4, "10", 1.0, 0.4),
            ("anim_steps.png", 4, "10", 1.0, 0.4),
            ("anim30.gif", 60, "100", 2.0, None),  # a GIF stores centiseconds: 30 fps is written as 3 / 4 / 3 cs
        )
        for name, frames, rate, duration, last in cases:
            with self.subTest(name=name):
                info = probe.probe(media(name), FFPROBE)
                self.assertEqual(info.kind, "video")
                self.assertEqual(info.extra["anim_rate"], rate)
                self.assertEqual(info.duration, duration)
                durations, _ = probe.animation_timing(media(name))
                self.assertEqual(len(durations), frames)
                self.assertEqual(sum(durations), Fraction(int(duration * 1000)))
                if last is not None:
                    self.assertAlmostEqual(info.extra["anim_last_s"], last, places=9)

    def test_rate_over_exact_durations(self):
        third = Fraction(100, 3)
        self.assertEqual(probe.animation_rate([third] * 60), "30")
        self.assertEqual(probe.animation_rate([33.333333333333336] * 3), "30")  # Pillow's float for 1/30 s
        self.assertEqual(probe.animation_rate([41.666666666666664, 83.33333333333333]), "24")
        self.assertEqual(probe.animation_rate([Fraction(1001, 30)] * 2), "30000/1001")
        self.assertIsNone(probe.animation_rate([third, Fraction(125, 3)]))  # a 25/3 ms grid is 120 fps: over the cap
        self.assertEqual(probe.animation_rate([100, 200, 300, 400]), "10")

    def test_gif_delays_are_read_without_decoding(self):
        for name in ("anim30.gif", "anim_mixed.gif", "anim_once.gif", "anim.gif"):
            with self.subTest(name=name):
                expected = decoded_durations(media(name))
                original = GifImagePlugin.GifImageFile.load
                with mock.patch.object(GifImagePlugin.GifImageFile, "load", autospec=True,
                                       side_effect=original) as load:
                    durations, _ = probe.animation_timing(media(name))
                self.assertEqual(load.call_count, 0)
                self.assertEqual(durations, expected)
        self.assertEqual(probe.animation_timing(media("anim_mixed.gif")),
                         ([Fraction(120), Fraction(0), Fraction(250), Fraction(30)], 0))

    def test_gif_falls_back_to_decoding_when_the_blocks_do_not_match(self):
        with mock.patch.object(probe, "_gif_delays", create=True, return_value=[Fraction(10)]):
            durations, plays = probe.animation_timing(media("anim_mixed.gif"))
        self.assertEqual(durations, decoded_durations(media("anim_mixed.gif")))
        self.assertEqual(plays, 0)

    def convert(self, name: str, fmt: str):
        info = probe.probe(media(name), FFPROBE)
        settings = catalog.normalize("video", {"format": fmt, "encoder": "cpu"}, caps())
        out = self.out_path(f"{Path(name).stem}_{Path(name).suffix[1:]}.{fmt}")
        video.run_video(media(name), info, settings, caps(), FFMPEG, out, self.paths.job_cache(out.stem), FakeCtx(),
                        None)
        return out

    def test_converted_animation_keeps_its_frames_and_time(self):
        """A 30 fps APNG becomes a WebP and an MP4 of the same 60 frames and 2.000 s."""
        for name in ("anim30.png",):
            with self.subTest(name=name, fmt="webp"):
                out = self.convert(name, "webp")
                durations = decoded_durations(out)
                self.assertEqual(len(durations), 60)
                # WebP stores whole ms and the encoder floors each frame start: 0, 33, 66, 100, ... 1966 + 33
                self.assertAlmostEqual(float(sum(durations)), 2000, delta=1)
            with self.subTest(name=name, fmt="mp4"):
                out = self.convert(name, "mp4")
                data = json.loads(subprocess.run(
                    [FFPROBE, "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                     "stream=nb_read_frames:format=duration", "-of", "json", str(out)],
                    capture_output=True, text=True, check=True).stdout)
                self.assertEqual(int(data["streams"][0]["nb_read_frames"]), 60)
                self.assertAlmostEqual(float(data["format"]["duration"]), 2.0, places=3)


if __name__ == "__main__":
    unittest.main()
