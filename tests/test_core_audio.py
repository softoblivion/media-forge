"""audio: formats, size-limited bitrate, lossless refusal, loudness normalisation, metadata stripping."""

from __future__ import annotations

import json
import re
import subprocess
import unittest

from media_forge.core import audio, catalog, probe, procs
from media_forge.core.errors import UserError

from tests.core_support import FFMPEG, FFPROBE, FakeCtx, ScratchCase, media, needs_ffmpeg


def ffprobe(path) -> dict:
    out = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                          str(path)], capture_output=True, check=True, creationflags=procs.CREATE_NO_WINDOW)
    return json.loads(out.stdout)


@needs_ffmpeg
class AudioTest(ScratchCase):
    def run_job(self, src: str, out_name: str, **settings):
        info = probe.probe(media(src), FFPROBE)
        s = catalog.normalize("audio", settings)
        out = self.out_path(out_name)
        ctx = FakeCtx()
        plan = audio.run_audio(media(src), info, s, FFMPEG, out, ctx)
        self.assertTrue(out.is_file() and out.stat().st_size > 0)
        return out, plan, ctx

    def test_formats(self):
        expected = {"mp3": "mp3", "m4a": "aac", "opus": "opus", "ogg": "vorbis", "flac": "flac", "wav": "pcm_s16le"}
        for fmt, codec in expected.items():
            with self.subTest(fmt=fmt):
                out, plan, ctx = self.run_job("tone.mp3", f"tone_out.{fmt}", format=fmt, bitrate_k=128)
                data = ffprobe(out)
                self.assertEqual(data["streams"][0]["codec_name"], codec)
                self.assertFalse(data["format"].get("tags", {}).get("title"))
                for stream in data["streams"]:
                    self.assertNotIn("encoder", {k.lower() for k in stream.get("tags", {})})
                self.assertIsNone(re.search(rb"Lav[fc]\d", out.read_bytes()))
                self.assertIn("encoding", ctx.stages)
                if fmt == "opus":
                    self.assertEqual(data["streams"][0]["sample_rate"], "48000")

    def test_metadata_kept_when_strip_off(self):
        out, _, _ = self.run_job("tone.mp3", "tagged.mp3", strip_metadata=False)
        tags = {k.lower(): v for k, v in ffprobe(out)["format"].get("tags", {}).items()}
        self.assertEqual(tags.get("title"), "Tone")

    def test_sample_rate_channels_normalize(self):
        out, plan, _ = self.run_job("tone.wav", "mono.mp3", channels="1", sample_rate="48000", normalize=True)
        stream = ffprobe(out)["streams"][0]
        self.assertEqual((stream["channels"], stream["sample_rate"]), (1, "48000"))
        self.assertIn("loudnorm=I=-16:TP=-1.5:LRA=11", plan.commands()[0])
        out, _, _ = self.run_job("tone.wav", "norm.flac", format="flac", normalize=True)
        self.assertEqual(ffprobe(out)["streams"][0]["sample_rate"], "44100")  # not loudnorm's 192 kHz

    def test_size_limited_mp3(self):
        src = media("music.flac")
        limit = 0.3  # 20 s -> 0.3 MB allows about 119 kbps -> 96
        out, plan, _ = self.run_job("music.flac", "limited.mp3", size_limit_mb=limit, bitrate_k=320)
        self.assertEqual(plan.audio_kbps, 96)
        self.assertLessEqual(out.stat().st_size, limit * 1024 * 1024)
        self.assertGreater(src.stat().st_size, out.stat().st_size)
        out, plan, _ = self.run_job("music.flac", "limited.opus", format="opus", size_limit_mb=limit)
        self.assertLessEqual(out.stat().st_size, limit * 1024 * 1024)

    def test_size_limit_rules(self):
        self.assertEqual(audio.size_limited_kbps(1.0, 60, "mp3"), (128, None))
        self.assertEqual(audio.size_limited_kbps(10.0, 60, "opus")[0], 256)
        kbps, note = audio.size_limited_kbps(0.1, 600, "mp3")
        self.assertEqual(kbps, 64)
        self.assertTrue(note)
        for fmt in ("flac", "wav"):
            with self.subTest(fmt=fmt), self.assertRaises(UserError) as cm:
                self.run_job("tone.wav", f"limited.{fmt}", format=fmt, size_limit_mb=1)
            self.assertEqual(cm.exception.message, "This format cannot be limited in size.")

    def test_limit_too_small(self):
        with self.assertRaises(UserError) as cm:
            self.run_job("music.flac", "toosmall.mp3", size_limit_mb=0.01)
        self.assertEqual(cm.exception.message, "The size limit could not be met at the lowest bitrate.")


if __name__ == "__main__":
    unittest.main()
