"""Audio jobs and audio extraction from a video (CORE.md 5.3, 3.2 "Audio extraction")."""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any, Mapping

from . import catalog
from .errors import (MSG_CANNOT_LIMIT, MSG_LIMIT_NOT_MET_AUDIO, MSG_NO_AUDIO, MSG_NO_DURATION, UserError)
from .ffjob import HEAD, FFmpegError, Plan, Step, metadata_args, run_steps
from .probe import ProbeInfo

log = logging.getLogger("media_forge.core.audio")

CODEC_ARGS = {
    "mp3": ["-c:a", "libmp3lame"],
    "m4a": ["-c:a", "aac"],
    "opus": ["-c:a", "libopus"],
    "ogg": ["-c:a", "libvorbis"],
    "flac": ["-c:a", "flac", "-compression_level", "8"],
    "wav": ["-c:a", "pcm_s16le"],
}
MUXERS = {"mp3": "mp3", "m4a": "ipod", "opus": "opus", "ogg": "ogg", "flac": "flac", "wav": "wav"}
LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"
SIZE_MARGIN = 0.97
MIN_KBPS = 64


def size_limited_kbps(limit_mb: float, duration: float, fmt: str) -> tuple[int, str | None]:
    """Largest bitrate of the list that fits `limit_mb` over `duration`; (kbps, note)."""
    kbps = math.floor(limit_mb * 8192 * SIZE_MARGIN / duration)
    allowed = [b for b in catalog.AUDIO_BITRATES if b <= kbps]
    if fmt == "opus":
        allowed = [b for b in allowed if b <= catalog.OPUS_MAX_KBPS]
    if not allowed:
        return MIN_KBPS, f"the limit allows {kbps} kbps; using the lowest bitrate, {MIN_KBPS} kbps"
    return allowed[-1], None


def _lower_bitrate(kbps: int) -> int | None:
    lower = [b for b in catalog.AUDIO_BITRATES if b < kbps]
    return lower[-1] if lower else None


def audio_settings_from_video(settings: Mapping[str, Any]) -> dict[str, Any]:
    """Audio-job settings for a video item whose format is audio-only (the video keys are ignored)."""
    return {"format": settings["format"], "bitrate_k": settings["audio_bitrate_k"],
            "size_limit_mb": settings.get("size_limit_mb"), "sample_rate": "keep", "channels": "keep",
            "normalize": False, "strip_metadata": settings.get("strip_metadata", True)}


def plan_audio(src: Path, info: ProbeInfo, settings: Mapping[str, Any], ffmpeg: str, out: Path,
               kbps_override: int | None = None, kind: str = "audio") -> Plan:
    fmt = settings["format"]
    if not info.has_audio:
        raise UserError(MSG_NO_AUDIO)
    plan = Plan(kind="audio" if kind == "audio" else "extract", output=out, duration=info.duration, encoder="")
    limit = settings.get("size_limit_mb")
    kbps: int | None = None
    if fmt not in catalog.LOSSLESS_AUDIO_FORMATS:
        kbps = int(settings["bitrate_k"])
        if fmt == "opus":
            kbps = min(kbps, catalog.OPUS_MAX_KBPS)
    if limit is not None:
        if fmt in catalog.LOSSLESS_AUDIO_FORMATS:
            raise UserError(MSG_CANNOT_LIMIT)
        if not info.duration:
            raise UserError(MSG_NO_DURATION)
        plan.size_limit_bytes = int(limit * 1024 * 1024)
        kbps, note = size_limited_kbps(float(limit), float(info.duration), fmt)
        if note:
            plan.notes.append(note)
    if kbps_override is not None:
        kbps = kbps_override
    plan.audio_kbps = kbps
    args = [ffmpeg] + HEAD + ["-i", str(src), "-map", "0:a:0", "-vn", "-sn", "-dn"]
    filters: list[str] = []
    if settings.get("normalize"):
        filters.append(LOUDNORM)
    if filters:
        args += ["-af", ",".join(filters)]
    rate = settings.get("sample_rate", "keep")
    out_rate: int | None = int(rate) if rate != "keep" else None
    if settings.get("normalize") and out_rate is None:
        out_rate = info.sample_rate or 48000  # loudnorm resamples to 192 kHz internally
    if fmt == "opus":
        if out_rate not in (None, 48000):
            plan.notes.append("Opus is always encoded at 48 kHz")
        out_rate = 48000
    if out_rate is not None:
        args += ["-ar", str(out_rate)]
    if settings.get("channels", "keep") != "keep":
        args += ["-ac", str(settings["channels"])]
    args += CODEC_ARGS[fmt]
    if kbps is not None:
        args += ["-b:a", f"{kbps}k"]
        if fmt == "opus" and limit is not None:
            args += ["-vbr", "constrained"]
    strip = bool(settings.get("strip_metadata", True))
    args += metadata_args(strip, video=False, audio=True)
    if fmt == "mp3" and strip:
        args += ["-id3v2_version", "0", "-write_xing", "0"]
    if fmt == "m4a":
        args += ["-movflags", "+faststart"]
    args += ["-f", MUXERS[fmt], str(out)]
    plan.steps.append(Step(args=args, stage="encoding"))
    return plan


def run_audio(src: Path, info: ProbeInfo, settings: Mapping[str, Any], ffmpeg: str, out: Path, ctx,
              kind: str = "audio") -> Plan:
    """Run the audio job; enforces the size cap with one retry at the next lower bitrate."""
    plan = plan_audio(src, info, settings, ffmpeg, out, kind=kind)
    run_steps(plan, ctx)
    if plan.size_limit_bytes is not None and out.stat().st_size > plan.size_limit_bytes:
        lower = _lower_bitrate(plan.audio_kbps or MIN_KBPS)
        if lower is None:
            raise UserError(MSG_LIMIT_NOT_MET_AUDIO)
        plan = plan_audio(src, info, settings, ffmpeg, out, kbps_override=lower, kind=kind)
        run_steps(plan, ctx, stage_override="retrying")
        if out.stat().st_size > plan.size_limit_bytes:
            raise UserError(MSG_LIMIT_NOT_MET_AUDIO)
    return plan


__all__ = ["plan_audio", "run_audio", "size_limited_kbps", "audio_settings_from_video", "FFmpegError"]
