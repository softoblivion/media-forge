"""Kind detection, metadata probe (ffprobe for video / audio, Pillow for images) and thumbnails (CORE.md 2).

`probe()` returns a `ProbeInfo`; `ProbeInfo.public()` is exactly `Item.probe` of BRIDGE_API.md 3.2, the other
fields are internal (pixel format, frame count, rotation, image mode) and used by the job planners.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps

from . import catalog, procs
from .errors import MSG_NO_FFMPEG, MSG_NOT_FOUND, MSG_NOT_MEDIA, UserError

log = logging.getLogger("media_forge.core.probe")

try:  # HEIC / HEIF through pillow-heif when it is installed
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:  # noqa: BLE001
    pillow_heif = None

Image.MAX_IMAGE_PIXELS = 400_000_000  # large panoramas are fine; decompression bombs beyond this are refused

THUMB_SIZE = 240
THUMB_QUALITY = 80
WAVE_COLOR = "#9fb8ff"
PROBE_TIMEOUT = 30.0
THUMB_TIMEOUT = 30.0
_IMAGE_PIPE_FORMATS = ("image2", "png_pipe", "jpeg_pipe", "webp_pipe", "bmp_pipe", "tiff_pipe", "gif_pipe",
                       "jpegxl_pipe", "avif", "heif")


@dataclass
class ProbeInfo:
    kind: str
    format: str | None = None
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    vcodec: str | None = None
    acodec: str | None = None
    bitrate: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    has_audio: bool = False
    # internal
    has_video: bool = False
    fps_str: str | None = None          # exact frame rate fraction for rawvideo pipes, e.g. "30000/1001"
    nb_frames: int | None = None
    pix_fmt: str | None = None
    rotation: int = 0
    audio_bitrate: int | None = None
    image_mode: str | None = None
    has_alpha: bool = False
    n_frames: int = 1
    extra: dict[str, Any] = field(default_factory=dict)

    PUBLIC = ("duration", "width", "height", "fps", "vcodec", "acodec", "bitrate", "sample_rate", "channels",
              "has_audio", "format")

    def public(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in self.PUBLIC}

    @property
    def ten_bit(self) -> bool:
        fmt = (self.pix_fmt or "").lower()
        return any(tag in fmt for tag in ("10le", "10be", "12le", "12be", "p010", "p016"))

    @property
    def total_frames(self) -> int | None:
        if self.nb_frames and self.nb_frames > 0:
            return self.nb_frames
        if self.duration and self.fps:
            return max(1, int(round(self.duration * self.fps)))
        return None


def detect_kind(path: str | os.PathLike) -> str | None:
    return catalog.kind_for_extension(path)


# --- probing ---------------------------------------------------------------------------------------------------

def _rate(text: str | None) -> float | None:
    if not text or text in ("0/0", "N/A"):
        return None
    try:
        value = float(Fraction(text))
    except (ValueError, ZeroDivisionError):
        return None
    return value if value > 0 else None


def _num(value: Any, cast=float):
    if value in (None, "", "N/A"):
        return None
    try:
        number = cast(float(value)) if cast is int else cast(value)
    except (TypeError, ValueError):
        return None
    return number


def _rotation(stream: dict) -> int:
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            try:
                return int(round(float(side["rotation"]))) % 360
            except (TypeError, ValueError):
                pass
    tag = (stream.get("tags") or {}).get("rotate")
    if tag:
        try:
            return int(tag) % 360
        except ValueError:
            pass
    return 0


def ffprobe_json(path: Path, ffprobe: str) -> dict | None:
    res = procs.run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                    capture=True, timeout=PROBE_TIMEOUT)
    if res.returncode != 0:
        return None
    try:
        data = json.loads(res.stdout.decode("utf-8", errors="replace"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def info_from_ffprobe(data: dict, size: int) -> ProbeInfo | None:
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    video = [s for s in streams if s.get("codec_type") == "video"
             and not (s.get("disposition") or {}).get("attached_pic")]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if not video and not audio:
        return None
    format_name = str(fmt.get("format_name") or "")
    info = ProbeInfo(kind="video" if video else "audio")
    info.format = ",".join(format_name.split(",")[:2]) or None
    info.duration = _num(fmt.get("duration"))
    info.has_audio = bool(audio)
    info.has_video = bool(video)
    if video:
        v = video[0]
        info.vcodec = v.get("codec_name")
        width, height = _num(v.get("width"), int), _num(v.get("height"), int)
        info.rotation = _rotation(v)
        if info.rotation in (90, 270) and width and height:
            width, height = height, width
        info.width, info.height = width, height
        rate = v.get("r_frame_rate")
        if not _rate(rate) or (_rate(rate) or 0) > 240:
            rate = v.get("avg_frame_rate")
        info.fps = _rate(rate)
        if info.fps:
            info.fps = round(info.fps, 3)
            info.fps_str = rate
        info.nb_frames = _num(v.get("nb_frames"), int)
        info.pix_fmt = v.get("pix_fmt")
        info.extra["color_space"] = v.get("color_space")
        if info.duration is None:
            info.duration = _num(v.get("duration"))
    if audio:
        a = audio[0]
        info.acodec = a.get("codec_name")
        info.sample_rate = _num(a.get("sample_rate"), int)
        info.channels = _num(a.get("channels"), int)
        info.audio_bitrate = _num(a.get("bit_rate"), int)
        if info.duration is None:
            info.duration = _num(a.get("duration"))
    if info.duration is not None:
        info.duration = round(info.duration, 3)
    info.bitrate = _num(fmt.get("bit_rate"), int)
    if not info.bitrate and info.duration and size:
        info.bitrate = int(size * 8 / info.duration)
    return info


def info_from_image(path: Path) -> ProbeInfo | None:
    try:
        with Image.open(path) as img:
            img_format = img.format
            mode = img.mode
            width, height = img.size
            n_frames = int(getattr(img, "n_frames", 1) or 1)
            try:
                orientation = img.getexif().get(0x0112, 1)
            except Exception:  # noqa: BLE001
                orientation = 1
            has_alpha = mode in ("RGBA", "LA", "PA") or (mode == "P" and "transparency" in img.info) \
                or (mode in ("RGB", "L") and "transparency" in img.info)
    except Exception:  # noqa: BLE001
        return None
    if orientation in (5, 6, 7, 8):
        width, height = height, width
    info = ProbeInfo(kind="image", format=img_format, width=width, height=height)
    info.image_mode = mode
    info.has_alpha = bool(has_alpha)
    info.n_frames = n_frames
    return info


def probe(path: str | os.PathLike, ffprobe: str | None) -> ProbeInfo:
    """Probe a file and decide its kind. Raises UserError with a sentence for the user."""
    p = Path(path)
    if not p.is_file():
        raise UserError(MSG_NOT_FOUND)
    size = p.stat().st_size
    ext_kind = detect_kind(p)
    if ext_kind == "image":
        info = info_from_image(p)
        if info is None:
            raise UserError(MSG_NOT_MEDIA)
        if info.format == "GIF" and info.n_frames > 1:
            if not ffprobe:
                raise UserError(MSG_NO_FFMPEG)
            data = ffprobe_json(p, ffprobe)
            video = info_from_ffprobe(data, size) if data else None
            if video is not None and video.kind == "video":
                video.format = "gif"
                return video
        return info
    if not ffprobe:
        if ext_kind is None:
            image = info_from_image(p)
            if image is not None:
                return image
        raise UserError(MSG_NO_FFMPEG)
    data = ffprobe_json(p, ffprobe)
    info = info_from_ffprobe(data, size) if data else None
    format_name = str(((data or {}).get("format") or {}).get("format_name") or "")
    if info is None or (format_name in _IMAGE_PIPE_FORMATS and not info.has_audio):
        image = info_from_image(p)
        if image is not None:
            return image
    if info is None:
        raise UserError(MSG_NOT_MEDIA)
    return info


# --- thumbnails ------------------------------------------------------------------------------------------------

def _data_url(img: Image.Image, keep_alpha: bool) -> str:
    buf = io.BytesIO()
    if keep_alpha:
        img.save(buf, format="PNG", optimize=True)
        mime = "image/png"
    else:
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=THUMB_QUALITY, optimize=True)
        mime = "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def to_8bit_gray(img: Image.Image) -> Image.Image:
    """16-bit / 32-bit grey images as 8-bit `L` (values scaled from the 16-bit range)."""
    arr = np.asarray(img).astype(np.float64)
    if img.mode == "F":
        arr = arr * 255.0 if arr.max(initial=0) <= 1.0 else arr
    else:
        arr = arr / 257.0
    return Image.fromarray(np.clip(np.rint(arr), 0, 255).astype(np.uint8))


def _image_thumb(path: Path) -> str | None:
    with Image.open(path) as img:
        if img.format == "JPEG":
            img.draft("RGB", (THUMB_SIZE * 2, THUMB_SIZE * 2))
        img = ImageOps.exif_transpose(img)
        if img.mode in ("I;16", "I;16B", "I;16L", "I", "F"):
            img = to_8bit_gray(img)
        alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
        img = img.convert("RGBA" if alpha else "RGB")
        img.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.LANCZOS)
        return _data_url(img, keep_alpha=alpha)


def _video_thumb(path: Path, info: ProbeInfo, ffmpeg: str) -> str | None:
    starts = [round((info.duration or 0) * 0.1, 3), 0.0]
    for start in dict.fromkeys(starts):
        args = [ffmpeg, "-hide_banner", "-v", "error"]
        if start > 0:
            args += ["-ss", f"{start}"]
        args += ["-i", str(path), "-map", "0:v:0", "-frames:v", "1",
                 "-vf", f"scale=w={THUMB_SIZE}:h={THUMB_SIZE}:force_original_aspect_ratio=decrease",
                 "-f", "image2pipe", "-c:v", "png", "-"]
        res = procs.run(args, capture=True, timeout=THUMB_TIMEOUT)
        if res.returncode == 0 and res.stdout:
            with Image.open(io.BytesIO(res.stdout)) as img:
                img = img.convert("RGB")
                img.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.LANCZOS)
                return _data_url(img, keep_alpha=False)
    return None


def _audio_thumb(path: Path, ffmpeg: str) -> str | None:
    args = [ffmpeg, "-hide_banner", "-v", "error", "-i", str(path), "-filter_complex",
            f"[0:a:0]showwavespic=s=480x240:colors={WAVE_COLOR}", "-frames:v", "1",
            "-f", "image2pipe", "-c:v", "png", "-"]
    res = procs.run(args, capture=True, timeout=THUMB_TIMEOUT * 4)
    if res.returncode != 0 or not res.stdout:
        return None
    with Image.open(io.BytesIO(res.stdout)) as img:
        img = img.convert("RGBA")
        img.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.LANCZOS)
        return _data_url(img, keep_alpha=True)


def thumbnail(path: str | os.PathLike, kind: str, info: ProbeInfo, ffmpeg: str | None) -> str | None:
    """Data URL of the thumbnail (long side 240 px), or None when it cannot be made."""
    p = Path(path)
    try:
        if kind == "image":
            return _image_thumb(p)
        if not ffmpeg:
            return None
        if kind == "video":
            return _video_thumb(p, info, ffmpeg)
        if kind == "audio":
            return _audio_thumb(p, ffmpeg)
    except Exception:  # noqa: BLE001  a thumbnail never fails the item
        log.exception("thumbnail failed for %s", p)
    return None
