"""Video jobs: filter chain, codec arguments, two-pass size targeting, GIF, audio extraction and the model-upscale
frame pipeline (CORE.md 3).
"""

from __future__ import annotations

import logging
import math
import time
from pathlib import Path
from typing import Any, Mapping

from . import catalog
from .audio import audio_settings_from_video, run_audio
from .errors import MSG_CANNOT_LIMIT, MSG_LIMIT_NOT_MET, MSG_NO_DURATION, MSG_NO_VIDEO, Cancelled, UserError
from .ffjob import HEAD, FFmpegError, Plan, Step, metadata_args, run_steps
from .probe import ProbeInfo
from .upscale import VIDEO_SHARPEN, Model, frame_pipeline, sharpen_amount

log = logging.getLogger("media_forge.core.video")

FACTORS = {"2x": 2, "4x": 4}
FITS = {"1080p": 1080, "720p": 720, "480p": 480}
RC_MAP = {"h264": (36.0, 0.21), "h265": (38.0, 0.22), "av1": (50.0, 0.30), "vp9": (45.0, 0.30)}
RC_RANGE = {"h264": (0, 51), "h265": (0, 51), "av1": (0, 63), "vp9": (0, 63)}
NVENC = {"h264": "h264_nvenc", "h265": "hevc_nvenc", "av1": "av1_nvenc"}
MUXERS = {"mp4": "mp4", "mkv": "matroska", "webm": "webm", "mov": "mov", "gif": "gif"}
# audio codecs a container takes as a stream copy (None = any)
AUDIO_COPY_OK: dict[str, set[str] | None] = {
    "mp4": {"aac", "mp3"},
    "mov": {"aac", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_s16be", "pcm_s24be"},
    "webm": {"opus", "vorbis"},
    "mkv": None,
}
BUDGET_MARGIN = 0.96
MIN_VIDEO_KBPS = 80
LOW_KBPS_FIT = 150
GIF_MAX_FPS = 15
GIF_PALETTE = "split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5"
SEI_FILTER = {"libx264": "filter_units=remove_types=6", "libx265": "filter_units=remove_types=39"}


def rc_value(codec: str, quality: int) -> int:
    a, b = RC_MAP[codec]
    lo, hi = RC_RANGE[codec]
    return int(min(hi, max(lo, round(a - b * quality))))


def prores_profile(quality: int) -> int:
    return 1 if quality < 40 else 2 if quality < 75 else 3


def resolve_encoder(codec: str, choice: str, caps: Mapping[str, Any] | None, force_cpu: bool = False) -> str:
    """`gpu` when NVENC is chosen or `auto` and the probe found it for this codec; else `cpu`."""
    if codec not in NVENC or force_cpu or choice == "cpu":
        return "cpu"
    nvenc = (caps or {}).get("nvenc") or {}
    return "gpu" if nvenc.get(codec) else "cpu"


def color_matrix(info: ProbeInfo) -> str:
    """Matrix used for the YUV <-> RGB conversions of the frame pipeline (the same on both sides)."""
    space = str(info.extra.get("color_space") or "").lower()
    if space in ("bt709",):
        return "bt709"
    if space in ("smpte170m", "bt470bg", "bt601"):
        return "bt601"
    return "bt709" if (info.height or 0) >= 720 or (info.width or 0) >= 1280 else "bt601"


def _even_fit(w: int, h: int, target: int) -> tuple[int, int] | None:
    """Dimensions with the short side `target` (never larger than the source); None when no scale is needed."""
    short = min(w, h)
    if short <= target:
        return None
    if w >= h:
        new_h = target - target % 2
        new_w = max(2, int(round(w * new_h / h / 2)) * 2)
    else:
        new_w = target - target % 2
        new_h = max(2, int(round(h * new_w / w / 2)) * 2)
    return new_w, new_h


def _audio_args(settings: Mapping[str, Any], info: ProbeInfo, fmt: str) -> tuple[list[str], float, bool]:
    """(ffmpeg audio args, audio kbps for the size budget, audio included)."""
    if fmt == "gif" or not info.has_audio or settings["audio"] == "none":
        return ["-an"], 0.0, False
    br = int(settings["audio_bitrate_k"])
    allowed = AUDIO_COPY_OK.get(fmt)
    if settings["audio"] == "keep" and (allowed is None or (info.acodec or "") in allowed):
        source_bps = info.audio_bitrate or ((info.bitrate or 0) * 0.1)
        return ["-c:a", "copy"], source_bps / 1000.0, True
    if fmt == "webm":
        return ["-c:a", "libopus", "-b:a", f"{br}k"], float(br), True
    return ["-c:a", "aac", "-b:a", f"{br}k"], float(br), True


def _codec_args(codec: str, encoder: str, quality: int, kbps: int | None, gop: int, strip: bool,
                fmt: str, pass_no: int | None = None) -> list[str]:
    if codec == "copy":
        return ["-c:v", "copy"]
    if codec == "prores":
        return ["-c:v", "prores_ks", "-profile:v", str(prores_profile(quality)), "-vendor", "apl0"]
    args: list[str]
    if encoder == "gpu":
        args = ["-c:v", NVENC[codec], "-preset", "p6"]
        if codec != "av1":
            args += ["-tune", "hq"]
        if kbps is None:
            args += ["-rc", "vbr", "-b:v", "0", "-cq", str(rc_value(codec, quality))]
            if codec != "av1":
                args += ["-multipass", "fullres"]
        else:
            args += ["-rc", "vbr", "-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.2)}k",
                     "-bufsize", f"{kbps * 2}k", "-multipass", "fullres"]
        args += ["-g", str(gop)]
    elif codec == "h264":
        args = ["-c:v", "libx264", "-preset", "slow"]
        args += _rate_args(codec, quality, kbps, pass_no)
        args += ["-bf", "3", "-g", str(gop)]
        if pass_no:
            args += ["-pass", str(pass_no), "-passlogfile", "x264pass"]
    elif codec == "h265":
        args = ["-c:v", "libx265", "-preset", "slow", "-x265-params", "log-level=error:info=0"]
        args += _rate_args(codec, quality, kbps, pass_no)
        args += ["-g", str(gop)]
        if pass_no:
            args += ["-pass", str(pass_no), "-x265-stats", "x265pass.log"]
    elif codec == "av1":
        args = ["-c:v", "libsvtav1", "-preset", "6"]
        if kbps is None:
            args += ["-crf", str(rc_value(codec, quality))]
        else:
            args += ["-b:v", f"{kbps}k"]
        args += ["-g", str(gop)]
    elif codec == "vp9":
        args = ["-c:v", "libvpx-vp9", "-row-mt", "1", "-cpu-used", "2"]
        if kbps is None:
            args += ["-b:v", "0", "-crf", str(rc_value(codec, quality))]
        else:
            args += ["-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.3)}k", "-bufsize", f"{kbps * 2}k"]
        args += ["-g", str(gop)]
    else:
        raise ValueError(f"unknown codec {codec!r}")
    if codec == "h265" and fmt in ("mp4", "mov"):
        args += ["-tag:v", "hvc1"]
    if strip and pass_no != 1:
        bsf = SEI_FILTER.get(args[1])
        if bsf:
            args += ["-bsf:v", bsf]
    return args


def _rate_args(codec: str, quality: int, kbps: int | None, pass_no: int | None) -> list[str]:
    if kbps is None:
        return ["-crf", str(rc_value(codec, quality))]
    return ["-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.3)}k", "-bufsize", f"{kbps * 2}k"]


def plan_video(src: Path, info: ProbeInfo, settings: Mapping[str, Any], caps: Mapping[str, Any] | None,
               ffmpeg: str, out: Path, workdir: Path, *, force_cpu: bool = False,
               video_kbps: int | None = None) -> Plan:
    """Decide every ffmpeg argument of a video job. Raises UserError for impossible requests."""
    fmt = settings["format"]
    if fmt in catalog.AUDIO_ONLY_VIDEO_FORMATS:
        from .audio import plan_audio
        return plan_audio(src, info, audio_settings_from_video(settings), ffmpeg, out, kind="video")
    if not info.has_video or not info.width or not info.height:
        raise UserError(MSG_NO_VIDEO)
    is_gif = fmt == "gif"
    codec = "gif" if is_gif else settings["codec"]
    strip = bool(settings["strip_metadata"])
    quality = int(settings["quality"])
    limit = settings.get("size_limit_mb")
    duration = info.duration
    w, h = int(info.width), int(info.height)
    plan = Plan(kind="gif" if is_gif else "encode", output=out, duration=duration, codec=codec)
    if limit is not None:
        if is_gif or codec in ("prores", "copy"):
            raise UserError(MSG_CANNOT_LIMIT)
        if not duration:
            raise UserError(MSG_NO_DURATION)
        plan.size_limit_bytes = int(limit * 1024 * 1024)
    encoder = "cpu" if is_gif else resolve_encoder(codec, settings["encoder"], caps, force_cpu)
    plan.encoder = encoder if codec in NVENC else "cpu"
    src_fps = info.fps or 30.0
    fps_choice = settings["fps"]
    fps_out = float(fps_choice) if fps_choice != "keep" else src_fps
    if is_gif:
        fps_out = min(GIF_MAX_FPS, fps_out)
    gop = max(1, int(round(2 * fps_out)))
    audio_args, audio_kbps, with_audio = _audio_args(settings, info, fmt)

    scale = settings["scale"]
    factor = FACTORS.get(scale)
    fit = FITS.get(scale)
    method = settings["upscale_method"] if factor else "lanczos"
    model_upscale = bool(factor) and method in ("fsrcnn", "edsr")

    kbps: int | None = None
    if plan.size_limit_bytes is not None:
        budget = float(limit) * 1024 * 1024 * 8 * BUDGET_MARGIN
        audio_bits = audio_kbps * 1000.0 * float(duration)
        kbps = max(MIN_VIDEO_KBPS, int(math.floor((budget - audio_bits) / float(duration) / 1000.0)))
        if video_kbps is not None:
            kbps = max(MIN_VIDEO_KBPS // 2, int(video_kbps))
        if kbps < LOW_KBPS_FIT and not factor and min(w, h) > 720 and (fit is None or fit > 720):
            fit = 720
            plan.notes.append(f"video bitrate {kbps} kbps is low; fitting the frame to 720p")
        plan.video_kbps = kbps

    # --- filter chain: scale -> unsharp -> format -> fps (GIF: -> palette) --------------------------------
    filters: list[str] = []
    out_w, out_h = w, h
    if codec != "copy":
        if factor:
            out_w, out_h = w * factor, h * factor
            if not model_upscale:
                filters.append(f"scale=iw*{factor}:ih*{factor}:flags=lanczos")
            if settings["sharpen"]:
                amount = sharpen_amount(VIDEO_SHARPEN, method, factor)
                filters.append(f"unsharp=5:5:{amount}:5:5:0.0")
        elif fit:
            dims = _even_fit(w, h, fit)
            if dims:
                out_w, out_h = dims
                filters.append(f"scale={out_w}:{out_h}:flags=lanczos")
        if not is_gif and not model_upscale and (out_w % 2 or out_h % 2):
            out_w, out_h = out_w - out_w % 2, out_h - out_h % 2
            filters.append(f"crop={out_w}:{out_h}:0:0")
        if is_gif:
            filters.append(f"fps={_fps_text(fps_out)}")
        else:
            filters.append("format=" + _pix_fmt(codec, encoder, info, model_upscale))
            if fps_choice != "keep":
                filters.append(f"fps={fps_choice}")
    vf = ",".join(filters)
    if is_gif:
        vf = (vf + "," if vf else "") + GIF_PALETTE

    meta = metadata_args(strip, video=True, audio=with_audio)
    tail_args: list[str] = []
    if fmt in ("mp4", "mov"):
        tail_args += ["-movflags", "+faststart"]
    if is_gif:
        tail_args += ["-loop", "0"]
    tail_args += ["-f", MUXERS[fmt], str(out)]
    hw = ["-hwaccel", "auto"] if encoder == "gpu" and not model_upscale else []

    if model_upscale:
        plan.kind = "pipeline"
        matrix = color_matrix(info)
        fps_str = info.fps_str or _fps_text(src_fps)
        decoder = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-i", str(src), "-map", "0:v:0", "-an", "-sn",
                   "-dn", "-vf", f"scale=in_color_matrix={matrix}:flags=lanczos+accurate_rnd+full_chroma_int",
                   "-fps_mode", "cfr", "-r", fps_str, "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
        enc_filters = [f"scale=out_color_matrix={matrix}:flags=lanczos+accurate_rnd+full_chroma_int"] + filters
        enc_vf = ",".join(enc_filters)
        if is_gif:
            enc_vf = ",".join(filters) + "," + GIF_PALETTE
        encoder_args = [ffmpeg, "-hide_banner", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                        "-s", f"{out_w}x{out_h}", "-r", fps_str, "-i", "-"]
        if with_audio:
            encoder_args += ["-i", str(src), "-map", "0:v:0", "-map", "1:a:0?"]
        else:
            encoder_args += ["-map", "0:v:0"]
        encoder_args += ["-vf", enc_vf]
        if not is_gif:
            encoder_args += _codec_args(codec, encoder, quality, kbps, gop, strip, fmt)
            encoder_args += ["-colorspace", matrix.replace("bt601", "smpte170m")]
        encoder_args += audio_args
        encoder_args += meta if strip else ["-map_metadata", "1" if with_audio else "-1"]
        encoder_args += tail_args
        plan.pipeline = {"decoder": decoder, "encoder": encoder_args, "width": w, "height": h, "factor": factor,
                         "method": method, "out_width": out_w, "out_height": out_h,
                         "frames": info.total_frames}
        plan.notes.append(f"{method} x{factor} on the CPU; {w}x{h} -> {out_w}x{out_h}")
        if kbps is not None:
            plan.notes.append(f"one pass at {kbps} kbps (size limit)")
        return plan

    head = [ffmpeg] + HEAD + hw + ["-i", str(src)]
    vmap = ["-map", "0:v:0"] + (["-map", "0:a:0?"] if with_audio else [])
    vf_args = ["-vf", vf] if vf else []
    if is_gif:
        plan.steps.append(Step(head + vmap + vf_args + ["-an"] + meta + tail_args, "encoding"))
        return plan
    two_pass = kbps is not None and encoder == "cpu" and codec in ("h264", "h265")
    if two_pass:
        pass1 = head + ["-map", "0:v:0"] + vf_args + _codec_args(codec, encoder, quality, kbps, gop, strip, fmt, 1) \
            + ["-an", "-f", "null", "-"]
        pass2 = head + vmap + vf_args + _codec_args(codec, encoder, quality, kbps, gop, strip, fmt, 2) \
            + audio_args + meta + tail_args
        plan.steps.append(Step(pass1, "pass 1", (0.0, 0.5), cwd=workdir))
        plan.steps.append(Step(pass2, "pass 2", (0.5, 1.0), cwd=workdir))
        plan.notes.append(f"two passes at {kbps} kbps (size limit)")
        return plan
    args = head + vmap + vf_args + _codec_args(codec, encoder, quality, kbps, gop, strip, fmt) + audio_args \
        + meta + tail_args
    plan.steps.append(Step(args, "encoding", cwd=workdir))
    if kbps is not None:
        plan.notes.append(f"one pass at {kbps} kbps (size limit)")
    return plan


def _fps_text(fps: float) -> str:
    return str(int(fps)) if float(fps).is_integer() else f"{fps:.3f}"


def _pix_fmt(codec: str, encoder: str, info: ProbeInfo, model_upscale: bool) -> str:
    if codec == "prores":
        return "yuv422p10le"
    if info.ten_bit and not model_upscale and codec in ("h265", "av1"):
        return "p010le" if encoder == "gpu" else "yuv420p10le"
    return "yuv420p"


# --- running ---------------------------------------------------------------------------------------------------

def _run_pipeline(plan: Plan, model: Model, ctx, stage: str = "upscaling") -> None:
    spec = plan.pipeline or {}
    total = spec.get("frames") or 0
    start = time.monotonic()
    ctx.report(progress=0.0, stage=stage)

    def on_frame(n: int) -> None:
        elapsed = max(1e-6, time.monotonic() - start)
        fps = n / elapsed
        progress = min(0.99, n / total) if total else None
        ctx.report(progress=progress, stage=stage, speed=f"{fps:.1f} fps" if fps < 10 else f"{fps:.0f} fps")

    res = frame_pipeline(spec["decoder"], spec["encoder"], spec["width"], spec["height"], model.upsample,
                         cancel=ctx.cancel, on_frame=on_frame)
    if res.cancelled or ctx.cancel.is_set():
        raise Cancelled()
    if res.write_failed or res.encoder_rc != 0:
        raise FFmpegError(res.encoder_rc, res.encoder_tail or res.decoder_tail, spec["encoder"])
    if res.decoder_rc != 0:
        raise FFmpegError(res.decoder_rc, res.decoder_tail, spec["decoder"])
    if res.frames == 0:
        raise FFmpegError(res.decoder_rc, res.decoder_tail or ["no video frames were decoded"], spec["decoder"])
    log.info("pipeline: %d frames in %.1f s (%.2f fps)", res.frames, time.monotonic() - start,
             res.frames / max(1e-6, time.monotonic() - start))


def _execute(plan: Plan, model: Model | None, ctx, stage_override: str | None = None) -> None:
    if plan.pipeline is not None:
        if model is None:
            raise RuntimeError("a model is required for this plan")
        _run_pipeline(plan, model, ctx, stage_override or "upscaling")
    else:
        run_steps(plan, ctx, stage_override)


def run_video(src: Path, info: ProbeInfo, settings: Mapping[str, Any], caps: Mapping[str, Any] | None,
              ffmpeg: str, out: Path, workdir: Path, ctx, model: Model | None = None) -> Plan:
    """Run a video job to completion: GPU start failure -> one CPU retry; size cap -> one corrective retry."""
    if settings["format"] in catalog.AUDIO_ONLY_VIDEO_FORMATS:
        return run_audio(src, info, audio_settings_from_video(settings), ffmpeg, out, ctx, kind="video")
    force_cpu = False
    plan = plan_video(src, info, settings, caps, ffmpeg, out, workdir)
    for note in plan.notes:
        log.info("plan: %s", note)
    try:
        _execute(plan, model, ctx)
    except FFmpegError as exc:
        if plan.encoder != "gpu" or not exc.is_gpu_start_failure():
            raise
        log.warning("NVENC failed to start; retrying on the CPU")
        force_cpu = True
        plan = plan_video(src, info, settings, caps, ffmpeg, out, workdir, force_cpu=True)
        _execute(plan, model, ctx, "retrying")
    if plan.size_limit_bytes is not None:
        size = out.stat().st_size
        if size > plan.size_limit_bytes:
            new_kbps = max(1, int((plan.video_kbps or MIN_VIDEO_KBPS) * plan.size_limit_bytes / size * 0.95))
            log.info("output %d B exceeds the cap %d B; retrying at %d kbps", size, plan.size_limit_bytes, new_kbps)
            plan = plan_video(src, info, settings, caps, ffmpeg, out, workdir, force_cpu=force_cpu,
                              video_kbps=new_kbps)
            _execute(plan, model, ctx, "retrying")
            if out.stat().st_size > plan.size_limit_bytes:
                raise UserError(MSG_LIMIT_NOT_MET)
    return plan
