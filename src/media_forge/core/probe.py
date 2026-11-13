"""Kind detection, metadata probe (ffprobe for video / audio, Pillow for images) and thumbnails (CORE.md 2).

`probe()` returns a `ProbeInfo`; `ProbeInfo.public()` is exactly `Item.probe` of BRIDGE_API.md 3.2, the other
fields are internal (pixel format, frame count, rotation, image mode) and used by the job planners.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import math
import mmap
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
# Pillow format of an image with more than one frame -> `ProbeInfo.format` of the video it becomes.
ANIMATED_IMAGES = {"GIF": "gif", "WEBP": "webp", "PNG": "apng"}
ANIMATED_CODECS = ("gif", "webp_anim", "apng")
MAX_ANIMATION_RATE = 100  # an animation's exact frame grid above this rate falls back to the probed rate
_APNG_MAX_DEN = 65535  # an APNG frame delay is delay_num / delay_den seconds, both 16-bit
ALPHA_PIX_FMTS = ("argb", "rgba", "abgr", "bgra", "yuva420p", "rgba64be", "rgba64le", "ya8", "ya16be", "ya16le")


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
    audio_sample_fmt: str | None = None   # ffprobe `sample_fmt` of the first audio stream (s16, s32, flt, fltp, ...)
    audio_bits: int | None = None         # bits per sample of a lossless source (24 for pcm_s24le / 24-bit FLAC)
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
        info.audio_sample_fmt = a.get("sample_fmt")
        # bits_per_raw_sample is set by lossless decoders (FLAC, PCM); bits_per_sample is the PCM container's width.
        # Lossy decoders report neither (0 or absent).
        bits = _num(a.get("bits_per_raw_sample"), int) or 0
        if not bits and str(info.acodec or "").startswith("pcm_"):
            bits = _num(a.get("bits_per_sample"), int) or 0
        info.audio_bits = bits or None
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


def exact_ms(value: Any) -> Fraction:
    """A frame duration in ms as an exact fraction. GIF and WebP store whole ms (or centiseconds); an APNG stores
    delay_num / delay_den seconds with a denominator of at most 65535, which Pillow hands over as a float of ms
    (33.333... for 1/30 s): the nearest fraction with such a denominator recovers the stored value."""
    if not value or value < 0:
        return Fraction(0)
    if isinstance(value, (int, Fraction)):
        return Fraction(value)
    return Fraction(float(value) / 1000).limit_denominator(_APNG_MAX_DEN) * 1000


def _gif_delays(path: Path) -> list[Fraction] | None:
    """Each GIF frame's delay in ms, read from the Graphic Control Extensions without decoding any image (Pillow
    decodes every earlier frame on a seek, CORE.md 2). A frame without such a block gets 0, as Pillow gives it.
    None when the block structure cannot be walked; the caller then decodes."""
    with open(path, "rb") as fh:
        if os.fstat(fh.fileno()).st_size < 13:
            return None
        with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as data:
            size = len(data)
            if data[:3] != b"GIF":
                return None

            def skip_blocks(pos: int) -> int:  # past a chain of data sub-blocks and its 0 terminator
                while pos < size:
                    length = data[pos]
                    pos += 1 + length
                    if length == 0:
                        return pos
                return size

            flags = data[10]
            pos = 13 + (3 << ((flags & 7) + 1) if flags & 0x80 else 0)
            delays: list[Fraction] = []
            delay: Fraction | None = None
            while pos < size:
                tag = data[pos]
                pos += 1
                if tag == 0x3B:  # trailer
                    break
                if tag == 0x21:  # extension: label, then sub-blocks
                    if pos >= size:
                        break
                    label = data[pos]
                    pos += 1
                    if label == 0xF9 and pos < size:  # graphic control: flags, delay (cs, little endian), index
                        if data[pos] < 3 or pos + 4 > size:
                            return None
                        delay = Fraction((data[pos + 2] | data[pos + 3] << 8) * 10)
                    pos = skip_blocks(pos)
                elif tag == 0x2C:  # image descriptor, optional local colour table, LZW code size, image data
                    if pos + 9 > size:
                        break
                    image_flags = data[pos + 8]
                    pos += 9 + (3 << ((image_flags & 7) + 1) if image_flags & 0x80 else 0) + 1
                    delays.append(delay if delay is not None else Fraction(0))
                    delay = None
                    pos = skip_blocks(pos)
                # any other byte is skipped, as Pillow's reader skips it
            return delays


def animation_timing(path: Path, frames: int | None = None) -> tuple[list[Fraction], int] | None:
    """(exact frame durations in ms, plays) of an animated image; plays 0 = for ever. None when unreadable.

    `frames` is Pillow's frame count when the caller already has it (counting walks a whole GIF again). GIF delays
    are read from the file's blocks without decoding; WebP and APNG frames are decoded, since Pillow sets their
    durations only then. A GIF without a loop block plays once; its loop value N repeats N times after the first
    play (N + 1 plays, as browsers show it). WebP and APNG count the plays themselves, 0 = for ever."""
    try:
        with Image.open(path) as img:
            frames = int(frames or getattr(img, "n_frames", 1) or 1)
            is_gif = img.format == "GIF"
            durations = _gif_delays(path) if is_gif else None
            if durations is None or len(durations) != frames:
                durations = []
                for index in range(frames):
                    img.seek(index)
                    img.load()  # the WebP reader sets a frame's duration when it decodes the frame
                    durations.append(exact_ms(img.info.get("duration")))
            loop = img.info.get("loop")
    except Exception:  # noqa: BLE001
        return None
    if is_gif:
        plays = 1 if loop is None else 0 if int(loop) == 0 else int(loop) + 1
    else:
        plays = max(0, int(loop or 0))
    return durations, plays


def animation_rate(durations: list) -> str | None:
    """The lowest frame rate on whose grid every frame boundary falls ("10" for 100 / 200 / 300 / 400 ms, "30" for
    frames of 100/3 ms), as an ffmpeg rate; None when that rate is above `MAX_ANIMATION_RATE` or a duration is
    unknown. Durations are ms, whole or exact fractions (`exact_ms`)."""
    exact = [exact_ms(d) for d in durations or ()]
    if not exact or any(d <= 0 for d in exact):
        return None
    den = math.lcm(*(d.denominator for d in exact))
    step = Fraction(math.gcd(*(int(d * den) for d in exact)), den)
    rate = 1000 / step
    if rate > MAX_ANIMATION_RATE:
        return None
    return str(rate.numerator) if rate.denominator == 1 else f"{rate.numerator}/{rate.denominator}"


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
        if info.format in ANIMATED_IMAGES and info.n_frames > 1:
            if not ffprobe:
                raise UserError(MSG_NO_FFMPEG)
            data = ffprobe_json(p, ffprobe)
            video = info_from_ffprobe(data, size) if data else None
            # Only a decoder that reads every frame makes it a video; an ffmpeg without the animated WebP decoder
            # reports a single `webp` image and the file stays a still, as before.
            if video is not None and video.kind == "video" and video.vcodec in ANIMATED_CODECS:
                video.format = ANIMATED_IMAGES[info.format]
                video.nb_frames = video.nb_frames or info.n_frames
                video.has_alpha = info.has_alpha or (video.pix_fmt or "") in ALPHA_PIX_FMTS
                timing = animation_timing(p, info.n_frames)
                if timing is not None:
                    durations, plays = timing
                    video.extra["anim_plays"] = plays
                    video.extra["anim_rate"] = animation_rate(durations)
                    if durations and durations[-1] > 0:
                        video.extra["anim_last_s"] = float(durations[-1]) / 1000.0
                    total = sum(durations)
                    if video.duration is None and total > 0:  # the WebP and APNG demuxers give none
                        video.duration = round(float(total) / 1000.0, 3)
                        if size:
                            video.bitrate = int(size * 8 / video.duration)
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
