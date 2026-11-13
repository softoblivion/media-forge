"""video: codecs, NVENC, GIF, audio extraction, scaling, model upscale, size limit, metadata stripping."""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from fractions import Fraction
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


def count_frames(path: Path, codec: str) -> int:
    """Decoded frame count of the first video stream, after checking its codec."""
    out = subprocess.run([FFPROBE, "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                          "stream=codec_name,nb_read_frames", "-of", "json", str(path)], capture_output=True,
                         check=True, creationflags=procs.CREATE_NO_WINDOW)
    stream = json.loads(out.stdout)["streams"][0]
    assert stream["codec_name"] == codec, stream
    return int(stream["nb_read_frames"])


def image_timing(path: Path) -> tuple[list[int], int | None]:
    """(frame durations in ms, loop value) of an animated image as Pillow reads them."""
    from PIL import Image

    with Image.open(path) as img:
        durations = []
        for index in range(getattr(img, "n_frames", 1)):
            img.seek(index)
            img.load()
            durations.append(int(round(float(img.info.get("duration") or 0))))
        return durations, img.info.get("loop")


def frame_times(path: Path) -> list[float]:
    """Presentation times (s) of the decoded frames of the first video stream."""
    out = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=pts_time",
                          "-of", "csv=p=0", str(path)], capture_output=True, check=True, text=True,
                         creationflags=procs.CREATE_NO_WINDOW)
    return [round(float(line), 3) for line in out.stdout.split()]


DURATION = 60.0
MEASURED = object()  # the faked packet reader returns the track's real size


def audio_of(plan) -> tuple[str, int | None]:
    """(`copy`, None), (`step`, kbps) for a re-encoded copied track, (`encoded`, kbps) or (`none`, None)."""
    if video._copies_audio(plan):
        return "copy", None
    note = next((n for n in plan.notes if n.startswith("audio re-encoded")), None)
    if note:
        return "step", int(note.split()[3])
    args = plan.commands()[-1]
    if "-b:a" in args:
        return "encoded", int(args[args.index("-b:a") + 1].rstrip("k"))
    return "none", None


class SizeCapPlanTest(unittest.TestCase):
    # name: (format, source codec, declared audio bps, overall bps, real track kbps, video overshoot per pass,
    #        cap MB, packet reader result, retry audio, retry video kbps (low, high), error)
    # pass 1 copies the track (or has none); "copy" keeps it in the retry, an int re-encodes it at that step
    CASES = {
        "a mkv, rate not declared, real 160": ("mkv", "aac", None, 1_000_000, 160, (1.0, 1.0), 10, MEASURED,
                                               128, (1214, 1214), None),
        "b declared and real 128, video 30 % over": ("mp4", "aac", 128_000, 3_000_000, 128, (1.3, 1.3), 10,
                                                     MEASURED, "copy", (880, 890), None),
        "c both over": ("mkv", "flac", None, 3_000_000, 1000, (1.2, 1.2), 10, MEASURED, 192, (905, 912), None),
        "d no audio": ("mp4", None, None, 3_000_000, 0, (1.2, 1.2), 10, MEASURED, "none", (1100, 1110), None),
        "e retry still over": ("mp4", "aac", 128_000, 3_000_000, 128, (1.3, 2.0), 10, MEASURED, "copy",
                               (880, 890), "limit"),
        "f cap below the audio alone": ("mp4", "aac", 128_000, 3_000_000, 128, (1.0, 1.0), 0.2, MEASURED, None,
                                        None, "limit"),
        "f2 cap below the video floor, no audio": ("mp4", None, None, 3_000_000, 0, (1.0, 1.0), 0.25, MEASURED,
                                                   None, None, "limit"),
        "g real far above declared": ("mp4", "aac", 128_000, 3_000_000, 400, (1.0, 1.0), 10, MEASURED, 192,
                                      (1150, 1150), None),
        "h real below the lowest step": ("mp4", "aac", 16_000, 3_000_000, 30, (1.1, 1.1), 10, MEASURED, "copy",
                                         (1175, 1190), None),
        "i measurement unknown": ("mp4", "aac", 128_000, 3_000_000, 128, (1.3, 1.3), 10, None, "copy",
                                  (880, 890), None),
        "i2 measurement empty": ("mp4", "aac", 128_000, 3_000_000, 128, (1.3, 1.3), 10, 0, "copy", (880, 890),
                                 None),
        "i3 measurement unknown, large track": ("mkv", "flac", None, 3_000_000, 1000, (1.0, 1.0), 10, None, "copy",
                                                (585, 600), "limit"),
    }

    def run_capped(self, fmt, acodec, audio_bps, bps, track_kbps, overshoot, cap_mb, measured):
        """run_video on a 60 s capped job, its passes faked: pass n writes the video at `overshoot[n]` times its
        planned rate plus the audio (`track_kbps` copied, the step or the chosen rate encoded).
        Returns (plans, sizes, error message or None)."""
        import tempfile
        from unittest import mock

        info = probe.ProbeInfo(kind="video", has_video=True, has_audio=acodec is not None, width=1920,
                               height=1080, duration=DURATION, fps=30.0, vcodec="h264", acodec=acodec, bitrate=bps,
                               audio_bitrate=audio_bps, sample_rate=48000, channels=2)
        s = catalog.normalize("video", dict(catalog.defaults("video"), format=fmt, audio="keep",
                                            size_limit_mb=cap_mb), None)
        plans, sizes = [], []
        real_bytes = int(track_kbps * 1000 / 8 * DURATION) if measured is MEASURED else measured
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / f"o.{fmt}"

            def fake_pass(plan, model, ctx, stage=None):
                plans.append(plan)
                how, kbps = audio_of(plan)
                audio = track_kbps if how == "copy" else kbps or 0
                sizes.append(int((plan.video_kbps * overshoot[len(plans) - 1] + audio) * 1000 / 8 * DURATION))
                out.write_bytes(b"\0" * sizes[-1])

            error = None
            with mock.patch.object(video, "_execute", fake_pass), \
                    mock.patch.object(video, "_audio_bytes", lambda ffmpeg, path: real_bytes):
                try:
                    video.run_video(Path(f"x.{fmt}"), info, s, None, "ffmpeg", out, Path(tmp), None)
                except UserError as exc:
                    error = exc.message
        return plans, sizes, error

    def test_the_size_cap_retry_per_case(self):
        cap_mb_error = "The size limit could not be met; try a lower resolution."
        for name, case in self.CASES.items():
            fmt, acodec, audio_bps, bps, track, overshoot, cap_mb, measured, audio, bounds, error = case
            with self.subTest(name):
                plans, sizes, got_error = self.run_capped(fmt, acodec, audio_bps, bps, track, overshoot, cap_mb,
                                                          measured)
                self.assertEqual(got_error, cap_mb_error if error else None)
                if audio is None:  # refused before any pass
                    self.assertEqual(plans, [])
                    continue
                self.assertEqual(len(plans), 2)  # one pass over the cap, at most one retry
                first, retry = plans
                cap = cap_mb * 1024 * 1024
                self.assertGreater(sizes[0], cap)
                self.assertEqual(audio_of(first)[0], "none" if acodec is None else "copy")
                how, step = audio_of(retry)
                self.assertEqual(step if how == "step" else how, audio)
                self.assertGreaterEqual(retry.video_kbps, bounds[0])
                self.assertLessEqual(retry.video_kbps, bounds[1])
                if how == "step":
                    self.assertLess(step, track)  # never at or above the track's real rate
                    self.assertLessEqual(retry.video_kbps, first.video_kbps + (track - step))
                else:
                    self.assertLess(retry.video_kbps, first.video_kbps)
                if not error:
                    self.assertLessEqual(sizes[1], cap)

    def test_the_cap_floor_lets_a_feasible_limit_run(self):
        """The refusal before the first pass holds only below the lowest audio step plus half the video floor."""
        floor_kbps = min(video.AUDIO_FIT_KBPS) + video.MIN_VIDEO_KBPS // 2
        cap_mb = floor_kbps * 1.01 * 1000 * DURATION / 8 / video.BUDGET_MARGIN / 1024 / 1024
        plans, _, _ = self.run_capped("mp4", "aac", 128_000, 3_000_000, 128, (0.5, 0.5), cap_mb, MEASURED)
        self.assertEqual(len(plans), 1)
        plans, _, error = self.run_capped("mp4", "aac", 128_000, 3_000_000, 128, (0.5, 0.5), cap_mb * 0.98,
                                          MEASURED)
        self.assertEqual((plans, error), ([], "The size limit could not be met; try a lower resolution."))

    def test_an_animation_without_a_grid_keeps_to_the_rate_cap(self):
        """Without `anim_rate` an animated source falls back to its probed rate, at most MAX_ANIMATION_RATE."""
        s = catalog.normalize("video", dict(catalog.defaults("video"), format="mp4"), None)
        for fps_str, extra, expected in (("200/1", {}, "fps=100"), ("25/1", {}, "fps=25/1"),
                                         ("200/1", {"anim_rate": "100/3"}, "fps=100/3")):
            with self.subTest(fps_str=fps_str, extra=extra):
                info = probe.ProbeInfo(kind="video", has_video=True, width=64, height=64, duration=1.0,
                                       fps=float(Fraction(fps_str)), fps_str=fps_str, vcodec="webp",
                                       extra={"anim_plays": 0, **extra})
                plan = video.plan_video(Path("x.webp"), info, s, None, "ffmpeg", Path("o.mp4"), Path("w"))
                vf = plan.commands()[0][plan.commands()[0].index("-vf") + 1]
                self.assertEqual(vf.split(",")[-1], expected)

    def test_loop_arguments_follow_the_play_count(self):
        self.assertEqual(video._loop_args("gif", 0), ["-loop", "0"])
        self.assertEqual(video._loop_args("gif", 1), ["-loop", "-1"])
        self.assertEqual(video._loop_args("gif", 3), ["-loop", "2"])
        self.assertEqual(video._loop_args("webp", 3), ["-loop", "3"])
        self.assertEqual(video._loop_args("apng", 0), ["-plays", "0"])
        self.assertEqual(video._loop_args("mp4", 3), [])

    def test_animation_rate_is_the_frame_grid(self):
        self.assertEqual(probe.animation_rate([100, 200, 300, 400]), "10")
        self.assertEqual(probe.animation_rate([40, 40, 80]), "25")
        self.assertEqual(probe.animation_rate([30, 40]), "100")
        self.assertEqual(probe.animation_rate([30, 70]), "100")
        self.assertIsNone(probe.animation_rate([33, 34]))  # a 1 ms grid: the probed rate is used
        self.assertIsNone(probe.animation_rate([100, 0]))


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

    def test_animated_images_keep_their_frames(self):
        """Animated WebP and APNG sources stay animated in every animated output, frame for frame."""
        cases = (("anim.webp", "gif", "gif"), ("anim.webp", "webp", "webp_anim"), ("anim.webp", "apng", "apng"),
                 ("anim.png", "webp", "webp_anim"), ("anim.png", "gif", "gif"), ("anim.gif", "webp", "webp_anim"))
        for src, fmt, codec in cases:
            with self.subTest(src=src, fmt=fmt):
                out_name = f"{src.replace('.', '_')}_to.{catalog.output_extension('video', {'format': fmt})}"
                out, plan, _ = self.run_job(src, out_name, format=fmt, quality=70)
                self.assertEqual(count_frames(out, codec), 12 if src != "anim.gif" else 10)
                self.assertEqual(plan.steps[0].args.count("-an"), 1)
        data = ffprobe(self.out / "anim_png_to.webp")
        self.assertEqual(vstream(data)["pix_fmt"], "argb")  # the APNG's transparency is kept
        self.assertTrue((self.out / "anim_webp_to.png").is_file())  # an animated PNG is written as .png
        with self.assertRaises(UserError) as cm:
            self.run_job("anim.webp", "capped.webp", format="webp", size_limit_mb=1)
        self.assertEqual(cm.exception.message, "This format cannot be limited in size.")

    def test_animated_frame_times_and_plays_are_kept(self):
        """Unequal frame times, the last one included, and the play count survive into every animated output and
        into a video; a GIF without a loop block stays a single play."""
        steps = [100, 200, 300, 400]
        for src in ("anim_steps.webp", "anim_steps.png"):
            for fmt in ("webp", "apng", "gif"):
                with self.subTest(src=src, fmt=fmt):
                    ext = catalog.output_extension("video", {"format": fmt})
                    out, _, _ = self.run_job(src, f"steps_{src.replace('.', '_')}_to.{ext}", format=fmt)
                    durations, loop = image_timing(out)
                    if fmt == "gif":  # the GIF output is resampled at a constant rate: the total time holds
                        self.assertEqual(sum(durations), sum(steps), durations)
                        self.assertEqual(loop, 2)  # a GIF counts repeats after the first play: 3 plays
                    else:
                        self.assertEqual(durations, steps)
                        self.assertEqual(loop, 3)
            with self.subTest(src=src, fmt="mp4"):
                out, _, _ = self.run_job(src, f"steps_{src.replace('.', '_')}_to.mp4", format="mp4", encoder="cpu")
                self.assertEqual(frame_times(out), [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9])
                self.assertAlmostEqual(float(vstream(ffprobe(out))["duration"]), 1.0, places=2)
        out, _, _ = self.run_job("anim_once.gif", "once_to.webp", format="webp")
        self.assertEqual(image_timing(out), (steps, 1))
        out, _, _ = self.run_job("anim_once.gif", "once_to.gif", format="gif")
        self.assertIsNone(image_timing(out)[1])  # no loop block: one play

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

    def test_size_limit_with_a_large_copied_audio_track(self):
        """Copied audio that leaves the video too little room is re-encoded lower instead of missing the cap."""
        limit = 1.0
        info = probe.probe(media("clip_pcm.mkv"), FFPROBE)
        self.assertGreater((info.audio_bitrate or 0) * info.duration / 8, limit * 1024 * 1024 * 0.9)
        out, plan, _ = self.run_job("clip_pcm.mkv", "pcm_capped.mkv", format="mkv", codec="h264", encoder="cpu",
                                    size_limit_mb=limit)
        self.assertLessEqual(out.stat().st_size, limit * 1024 * 1024)
        audio = astreams(ffprobe(out))[0]
        self.assertEqual(audio["codec_name"], "aac")
        self.assertTrue(any("audio re-encoded" in note for note in plan.notes), plan.notes)
        # the same cap with room to spare keeps the copied track
        out, plan, _ = self.run_job("clip_pcm.mkv", "pcm_roomy.mkv", format="mkv", codec="h264", encoder="cpu",
                                    size_limit_mb=4.0)
        self.assertEqual(astreams(ffprobe(out))[0]["codec_name"], "pcm_s16le")

    def test_the_audio_stream_size_is_read_from_its_packets(self):
        src = media("clip_hq.mp4")
        out = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a:0", "-show_entries", "packet=size",
                              "-of", "csv=p=0", str(src)], capture_output=True, check=True, text=True,
                             creationflags=procs.CREATE_NO_WINDOW)
        expected = sum(int(line.split(",")[0]) for line in out.stdout.split())  # side data adds cells
        self.assertGreater(expected, 0)
        self.assertEqual(video._audio_bytes(FFMPEG, src), expected)
        self.assertIsNone(video._audio_bytes(FFMPEG, self.out_path("missing.mp4")))

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
