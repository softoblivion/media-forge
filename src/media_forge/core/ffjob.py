"""Shared pieces of the ffmpeg jobs: plan / step types, the step runner with progress, metadata arguments and the
mapping of ffmpeg errors to sentences for the user (CORE.md 3, 5.3, 6).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from . import procs
from .errors import MSG_DAMAGED_INPUT, MSG_DISK_FULL, MSG_UNSUPPORTED_CODEC, MSG_WRITE_DENIED, Cancelled

log = logging.getLogger("media_forge.core.ffjob")

HEAD = ["-hide_banner", "-nostdin", "-nostats", "-v", "error", "-y", "-progress", "pipe:1"]

GPU_START_ERRORS = ("no capable devices", "cannot load nvcuda.dll", "openencodesessionex failed",
                    "no nvenc capable devices", "cannot init cuda", "driver does not support the required nvenc",
                    "minimum required nvidia driver")


class Context(Protocol):
    cancel: threading.Event

    def report(self, progress: float | None = None, stage: str | None = None, speed: str | None = None) -> None:
        ...


@dataclass
class Step:
    args: list[str]
    stage: str
    span: tuple[float, float] = (0.0, 1.0)
    cwd: Path | None = None


@dataclass
class Plan:
    """What a video or audio job will run. `steps` are ffmpeg invocations in order; a model-upscale video uses
    `pipeline` instead (decoder and encoder command lines, see video.py)."""
    kind: str                       # "encode" | "gif" | "extract" | "audio" | "pipeline"
    output: Path
    steps: list[Step] = field(default_factory=list)
    duration: float | None = None
    encoder: str = "cpu"            # "cpu" | "gpu" (video), "" otherwise
    codec: str | None = None
    video_kbps: int | None = None
    audio_kbps: int | None = None
    size_limit_bytes: int | None = None
    notes: list[str] = field(default_factory=list)
    pipeline: dict | None = None

    def commands(self) -> list[list[str]]:
        cmds = [step.args for step in self.steps]
        if self.pipeline:
            cmds = [self.pipeline["decoder"], self.pipeline["encoder"]]
        return cmds

    def describe(self) -> str:
        lines = [f"# {note}" for note in self.notes]
        if self.pipeline:
            lines.append("# decoder | model upscale (" + self.pipeline["method"] + f" x{self.pipeline['factor']}) | encoder")
        lines += [procs.format_cmd(cmd) for cmd in self.commands()]
        return "\n".join(lines)


class FFmpegError(Exception):
    def __init__(self, returncode: int | None, tail: list[str], args: list[str]) -> None:
        super().__init__(f"ffmpeg exited with {returncode}")
        self.returncode = returncode
        self.tail = tail
        self.args_list = args

    def is_gpu_start_failure(self) -> bool:
        text = "\n".join(self.tail).lower()
        return any(marker in text for marker in GPU_START_ERRORS)


def metadata_args(strip: bool, video: bool, audio: bool) -> list[str]:
    """Strip: no global / stream metadata, no chapters, no encoder tags, bit-exact muxers and encoders."""
    if not strip:
        return ["-map_metadata", "0"]
    args = ["-map_metadata", "-1", "-map_chapters", "-1", "-fflags", "+bitexact"]
    if video:
        args += ["-flags:v", "+bitexact", "-metadata:s:v:0", "encoder="]
    if audio:
        args += ["-flags:a", "+bitexact", "-metadata:s:a:0", "encoder="]
    return args


def speed_label(factor: float | None) -> str | None:
    if factor is None:
        return None
    return f"{factor:.1f}x" if factor < 100 else f"{factor:.0f}x"


def run_step(step: Step, duration: float | None, ctx: Context, stage: str | None = None) -> None:
    """Run one ffmpeg step; progress inside the step's span from `out_time_us / duration`."""
    lo, hi = step.span
    shown_stage = stage or step.stage
    ctx.report(progress=lo, stage=shown_stage)

    def on_block(block: dict[str, str]) -> None:
        seconds = procs.out_time_seconds(block)
        frac = None
        if seconds is not None and duration:
            frac = max(0.0, min(1.0, seconds / duration))
        if block.get("progress") == "end":
            frac = 1.0
        ctx.report(progress=None if frac is None else lo + (hi - lo) * frac, stage=shown_stage,
                   speed=speed_label(procs.speed_factor(block)))

    parser = procs.ProgressParser(on_block)
    res = procs.run(step.args, on_line=parser.feed, cancel=ctx.cancel, cwd=step.cwd)
    if res.cancelled or ctx.cancel.is_set():
        raise Cancelled()
    if res.returncode != 0:
        tail = res.stderr or ([res.start_error] if res.start_error else [])
        raise FFmpegError(res.returncode, tail, step.args)


def run_steps(plan: Plan, ctx: Context, stage_override: str | None = None) -> None:
    for step in plan.steps:
        run_step(step, plan.duration, ctx, stage_override)


def error_message(tail: list[str]) -> str:
    """One sentence for the user from ffmpeg's last stderr lines."""
    text = "\n".join(tail).lower()
    if "no space left" in text or "disk full" in text:
        return MSG_DISK_FULL
    if "permission denied" in text or "access is denied" in text or "being used by another process" in text:
        return MSG_WRITE_DENIED
    if "invalid data found" in text or "moov atom not found" in text or "could not find codec parameters" in text:
        return MSG_DAMAGED_INPUT
    if "could not find tag for codec" in text or "not currently supported in container" in text \
            or "unknown encoder" in text or "encoder not found" in text:
        return MSG_UNSUPPORTED_CODEC
    last = ""
    for line in reversed(tail):
        if line.strip():
            last = line.strip()
            break
    if not last:
        return "ffmpeg failed without an error message."
    message = f"ffmpeg failed: {last}"
    if len(message) > 160:
        message = message[:157].rstrip() + "..."
    if not message.endswith((".", "...")):
        message += "."
    return message

