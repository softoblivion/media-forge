"""video: codecs, NVENC, GIF, audio extraction, scaling, model upscale, size limit, metadata stripping."""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

from media_forge.core import catalog, ffjob, procs, probe, video
from media_forge.core.errors import UserError
from media_forge.core.paths import AppPaths
from media_forge.core.upscale import Model

from tests.core_support import FFMPEG, FFPROBE, FakeCtx, ScratchCase, caps, media, needs_ffmpeg, nvenc


def ffprobe(path: Path) -> dict:
    out = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                          str(path)], capture_output=True, check=True, creationflags=procs.CREATE_NO_WINDOW)
    return json.loads(out.stdout)


def vstream(data: dict) -> dict:
    return next(s for s in data["streams"] if s["codec_type"] == "video")


def astreams(data: dict) -> list[dict]:
    return [s for s in data["streams"] if s["codec_type"] == "audio"]


@needs_ffmpeg
class VideoTest(ScratchCase):
    def run_job(self, src: str, out_name: str, model: Model | None = None, caps_override=None, **settings):
        info = probe.probe(media(src), FFPROBE)
        s = catalog.normalize("video", settings, caps_override if caps_override is not None else caps())
        out = self.out_path(out_name)
        ctx = FakeCtx()
        workdir = self.paths.job_cache(out_name.replace(".", "_"))
        plan = video.run_video(media(src), info, s, caps_override if caps_override is not None else caps(),
                               FFMPEG, out, workdir, ctx, model)
        self.assertTrue(out.is_file() and out.stat().st_size > 0, out)
        return out, plan, ctx

    def assert_clean(self, out: Path) -> None:
        data = ffprobe(out)
        self.assertFalse(data["format"].get("tags", {}).get("title"))
        self.assertFalse(data["format"].get("tags", {}).get("comment"))
        fmt_tags = {k.lower(): v for k, v in data["format"].get("tags", {}).items()}
        if "matroska" in data["format"]["format_name"]:
            # MuxingApp / WritingApp are mandatory Matroska elements; bit-exact mode writes "Lavf" without version
            self.assertIn(fmt_tags.get("encoder"), (None, "Lavf"))
        else:
            self.assertNotIn("encoder", fmt_tags)
        for stream in data["streams"]:
            self.assertNotIn("encoder", {k.lower() for k in stream.get("tags", {})}, stream)
        raw = out.read_bytes()
        for needle in (b"x264 - core", b"x265 (build", b"Lavc", b"libx26"):
            self.assertFalse(needle in raw, f"{needle!r} in {out.name}")
        self.assertIsNone(re.search(rb"Lav[fc]\d", raw), out.name)

    # --- codecs ----------------------------------------------------------------------------------------------
    def test_h264_cpu(self):
        out, plan, ctx = self.run_job("clip.mp4", "h264_cpu.mp4", encoder="cpu", quality=60)
        data = ffprobe(out)
        self.assertEqual(vstream(data)["codec_name"], "h264")
        self.assertEqual(vstream(data)["pix_fmt"], "yuv420p")
        self.assertEqual(astreams(data)[0]["codec_name"], "aac")
        self.assertEqual(plan.encoder, "cpu")
        self.assertIn("encoding", ctx.stages)
        self.assertTrue(any(p == 1.0 for p, _, _ in ctx.reports))
        self.assert_clean(out)
        self.assertIn("-crf", plan.commands()[0])
        self.assertEqual(plan.commands()[0][plan.commands()[0].index("-crf") + 1], "23")

    def test_h265_cpu(self):
        out, plan, _ = self.run_job("clip.mp4", "h265.mp4", codec="h265", encoder="cpu")
        stream = vstream(ffprobe(out))
        self.assertEqual(stream["codec_name"], "hevc")
        self.assertEqual(stream["codec_tag_string"], "hvc1")
        self.assert_clean(out)

    def test_av1_cpu_mkv(self):
        out, _, _ = self.run_job("clip.mp4", "av1.mkv", format="mkv", codec="av1", encoder="cpu")
        self.assertEqual(vstream(ffprobe(out))["codec_name"], "av1")
        self.assert_clean(out)

    def test_vp9_webm(self):
        out, _, _ = self.run_job("clip.mp4", "vp9.webm", format="webm", codec="vp9")
        data = ffprobe(out)
        self.assertEqual(vstream(data)["codec_name"], "vp9")
        self.assertEqual(astreams(data)[0]["codec_name"], "opus")  # aac source re-encoded for WebM
        self.assert_clean(out)

    def test_prores_mov(self):
        out, _, _ = self.run_job("clip.mp4", "prores.mov", format="mov", codec="prores", quality=50)
        stream = vstream(ffprobe(out))
        self.assertEqual(stream["codec_name"], "prores")
        self.assertEqual(stream["profile"], "Standard")
        self.assert_clean(out)

    def test_copy_mkv(self):
        out, plan, _ = self.run_job("clip.mp4", "copy.mkv", format="mkv", codec="copy")
        self.assertIn("copy", plan.commands()[0])
        data = ffprobe(out)
        self.assertEqual(vstream(data)["codec_name"], "h264")
        self.assertEqual(astreams(data)[0]["codec_name"], "aac")

    def test_nvenc_each_codec(self):
        available = nvenc()
        for codec, fmt in (("h264", "mp4"), ("h265", "mp4"), ("av1", "mkv")):
            with self.subTest(codec=codec):
                if not available[codec]:
                    self.skipTest(f"NVENC {codec} is not available on this machine")
                out, plan, _ = self.run_job("clip.mp4", f"nvenc_{codec}.{fmt}", format=fmt, codec=codec,
                                            encoder="gpu")
                self.assertEqual(plan.encoder, "gpu")
                self.assertIn(video.NVENC[codec], plan.commands()[0])
                self.assertEqual(vstream(ffprobe(out))["codec_name"], {"h265": "hevc"}.get(codec, codec))
                self.assert_clean(out)

    def test_auto_without_nvenc_uses_cpu(self):
        no_gpu = {**caps(), "nvenc": {"h264": False, "h265": False, "av1": False}}
        _, plan, _ = self.run_job("clip.mp4", "auto_cpu.mp4", caps_override=no_gpu, encoder="auto")
        self.assertEqual(plan.encoder, "cpu")
        self.assertIn("libx264", plan.commands()[0])

    def test_gpu_failure_markers(self):
        err = ffjob.FFmpegError(1, ["[h264_nvenc @ 0x1] OpenEncodeSessionEx failed: no encode device (1)"], [])
        self.assertTrue(err.is_gpu_start_failure())
        self.assertFalse(ffjob.FFmpegError(1, ["Invalid data found when processing input"], []).is_gpu_start_failure())

    # --- gif, audio, scaling ---------------------------------------------------------------------------------
    def test_gif(self):
        out, _, _ = self.run_job("clip.mp4", "anim.gif", format="gif", fps="30")
        data = ffprobe(out)
        stream = vstream(data)
        self.assertEqual(stream["codec_name"], "gif")
        self.assertLessEqual(probe._rate(stream["r_frame_rate"]), 15.01)
        self.assertLessEqual(int(stream["nb_frames"]), 31)
        self.assertEqual(astreams(data), [])

    def test_audio_extraction(self):
        out, plan, _ = self.run_job("clip.mp4", "extract.mp3", format="mp3", audio_bitrate_k=128)
        data = ffprobe(out)
        self.assertEqual([s["codec_type"] for s in data["streams"]], ["audio"])
        self.assertEqual(data["streams"][0]["codec_name"], "mp3")
        self.assertEqual(plan.kind, "extract")
        self.assertNotIn(b"ID3", out.read_bytes()[:3])
        with self.assertRaises(UserError) as cm:
            self.run_job("noaudio.mkv", "none.mp3", format="mp3")
        self.assertEqual(cm.exception.message, "The file has no audio track.")

    def test_lanczos_2x(self):
        out, plan, _ = self.run_job("clip.mp4", "lanczos2x.mp4", scale="2x", encoder="cpu")
        stream = vstream(ffprobe(out))
        self.assertEqual((stream["width"], stream["height"]), (640, 480))
        self.assertIn("scale=iw*2:ih*2:flags=lanczos,unsharp=5:5:0.6:5:5:0.0,format=yuv420p", plan.commands()[0])

    def test_fit_never_enlarges_and_even_crop(self):
        out, _, _ = self.run_job("clip.mp4", "fit480_small.mp4", scale="480p", encoder="cpu")
        stream = vstream(ffprobe(out))
        self.assertEqual((stream["width"], stream["height"]), (320, 240))
        out, _, _ = self.run_job("odd.mp4", "odd.mp4", encoder="cpu")
        stream = vstream(ffprobe(out))
        self.assertEqual((stream["width"], stream["height"]), (320, 240))

    def test_fit_downscale_and_fps(self):
        out, _, _ = self.run_job("hd.mp4", "fit480.mp4", scale="480p", encoder="cpu")
        stream = vstream(ffprobe(out))
        self.assertEqual((stream["width"], stream["height"]), (854, 480))
        out, _, _ = self.run_job("clip.mp4", "fps24.mp4", fps="24", encoder="cpu")
        self.assertAlmostEqual(probe._rate(vstream(ffprobe(out))["avg_frame_rate"]), 24.0, delta=0.1)
        dims = video._even_fit(1920, 1080, 720)
        self.assertEqual(dims, (1280, 720))
        self.assertEqual(video._even_fit(1080, 1920, 480), (480, 854))
        self.assertIsNone(video._even_fit(640, 360, 480))

    def test_rotation_applied(self):
        out, _, _ = self.run_job("rotated.mp4", "rotated_out.mp4", encoder="cpu")
        stream = vstream(ffprobe(out))
        self.assertEqual((stream["width"], stream["height"]), (240, 320))

    def test_fsrcnn_2x_pipeline(self):
        model = Model.load("fsrcnn", 2, AppPaths(home=self.paths.home))
        out, plan, ctx = self.run_job("small.mp4", "fsrcnn2x.mp4", model=model, scale="2x",
                                      upscale_method="fsrcnn", encoder="cpu")
        self.assertEqual(plan.kind, "pipeline")
        stream = vstream(ffprobe(out))
        self.assertEqual((stream["width"], stream["height"]), (320, 180))
        self.assertEqual(int(stream["nb_frames"]), 10)
        self.assertIn("upscaling", ctx.stages)
        self.assertTrue(any(s and s.endswith("fps") for _, _, s in ctx.reports))
        self.assert_clean(out)

    def test_fsrcnn_4x_gif_pipeline(self):
        model = Model.load("fsrcnn", 4, AppPaths(home=self.paths.home))
        out, _, _ = self.run_job("small.mp4", "fsrcnn4x.gif", model=model, format="gif", scale="4x",
                                 upscale_method="fsrcnn")
        stream = vstream(ffprobe(out))
        self.assertEqual((stream["width"], stream["height"]), (640, 360))

    # --- size limit ------------------------------------------------------------------------------------------
    def test_two_pass_size_limit(self):
        src_size = media("clip_hq.mp4").stat().st_size
        limit = 0.6
        self.assertGreater(src_size, limit * 1024 * 1024 * 2)
        out, plan, ctx = self.run_job("clip_hq.mp4", "limited.mp4", size_limit_mb=limit, encoder="cpu")
        self.assertLessEqual(out.stat().st_size, limit * 1024 * 1024)
        self.assertEqual(len(plan.steps), 2)
        self.assertIn("pass 1", ctx.stages)
        self.assertIn("pass 2", ctx.stages)
        self.assert_clean(out)

    def test_size_limit_h265_and_vp9(self):
        for codec, fmt in (("h265", "mp4"), ("vp9", "webm")):
            with self.subTest(codec=codec):
                out, _, _ = self.run_job("clip_hq.mp4", f"limited_{codec}.{fmt}", format=fmt, codec=codec,
                                         size_limit_mb=0.5, encoder="cpu")
                self.assertLessEqual(out.stat().st_size, 0.5 * 1024 * 1024)

    def test_size_limit_nvenc(self):
        if not nvenc()["h264"]:
            self.skipTest("NVENC h264 is not available on this machine")
        out, plan, _ = self.run_job("clip_hq.mp4", "limited_nvenc.mp4", size_limit_mb=0.5, encoder="gpu")
        self.assertEqual(plan.encoder, "gpu")
        self.assertLessEqual(out.stat().st_size, 0.5 * 1024 * 1024)

    def test_size_limit_refused(self):
        for settings in ({"format": "gif"}, {"format": "mov", "codec": "prores"}):
            with self.subTest(**settings), self.assertRaises(UserError) as cm:
                self.run_job("clip.mp4", "refused.out", size_limit_mb=1, **settings)
            self.assertEqual(cm.exception.message, "This format cannot be limited in size.")

    # --- metadata kept when asked --------------------------------------------------------------------------
    def test_metadata_kept_when_strip_off(self):
        out, _, _ = self.run_job("clip.mp4", "keep_meta.mkv", format="mkv", strip_metadata=False)
        tags = {k.lower(): v for k, v in ffprobe(out)["format"].get("tags", {}).items()}
        self.assertEqual(tags.get("title"), "Test clip")

    def test_crf_mapping(self):
        self.assertEqual(video.rc_value("h264", 70), 21)
        self.assertEqual(video.rc_value("h264", 100), 15)
        self.assertEqual(video.rc_value("h265", 70), 23)
        self.assertEqual(video.rc_value("av1", 70), 29)
        self.assertEqual(video.rc_value("vp9", 0), 45)
        self.assertEqual([video.prores_profile(q) for q in (10, 50, 90)], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
