"""Option tables, default settings, normalisation and output names (BRIDGE_API.md 3.3, 3.4, 3.7; CORE.md 5.1).

`options(caps)` is the only source of the lists the UI renders; `normalize(kind, settings, caps)` is the only
place that decides which settings are valid. Both are pure functions of their arguments.
"""

from __future__ import annotations

import copy
import math
import os
from pathlib import Path
from typing import Any, Mapping

KINDS = ("video", "image", "audio")

# --- extensions (CORE.md 2) ------------------------------------------------------------------------------------

VIDEO_EXTS = (".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".flv", ".ts", ".m2ts", ".mts", ".mpg",
              ".mpeg", ".3gp")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".avif", ".heic", ".heif", ".tif", ".tiff", ".bmp", ".gif", ".jxl")
AUDIO_EXTS = (".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma", ".aiff", ".aif")
MEDIA_EXTS = VIDEO_EXTS + IMAGE_EXTS + AUDIO_EXTS


def kind_for_extension(path: str | os.PathLike) -> str | None:
    """Kind by extension alone. `.gif` is reported as image; the probe turns an animated GIF into a video."""
    ext = os.path.splitext(str(path))[1].lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in AUDIO_EXTS:
        return "audio"
    return None


def dialog_filters() -> list[tuple[str, list[str]]]:
    """Open-dialog filters for the shell: (label, extensions without dot)."""
    def strip(exts: tuple[str, ...]) -> list[str]:
        return [e[1:] for e in exts]
    return [("Media", strip(MEDIA_EXTS)), ("Video", strip(VIDEO_EXTS + (".gif",))), ("Images", strip(IMAGE_EXTS)),
            ("Audio", strip(AUDIO_EXTS)), ("All", ["*"])]


# --- option tables (BRIDGE_API.md 3.7) -------------------------------------------------------------------------

VIDEO_FORMATS = [
    {"id": "mp4", "label": "MP4", "codecs": ["h264", "h265", "av1"], "audio": True},
    {"id": "mkv", "label": "MKV", "codecs": ["h264", "h265", "av1", "vp9", "copy"], "audio": True},
    {"id": "webm", "label": "WebM", "codecs": ["vp9", "av1"], "audio": True},
    {"id": "mov", "label": "MOV", "codecs": ["h264", "h265", "prores"], "audio": True},
    {"id": "gif", "label": "GIF", "codecs": [], "audio": False},
    {"id": "mp3", "label": "MP3 · audio only", "codecs": [], "audio": True},
    {"id": "m4a", "label": "M4A · audio only", "codecs": [], "audio": True},
    {"id": "opus", "label": "Opus · audio only", "codecs": [], "audio": True},
    {"id": "flac", "label": "FLAC · audio only", "codecs": [], "audio": True},
    {"id": "wav", "label": "WAV · audio only", "codecs": [], "audio": True},
]
VIDEO_CODECS = [
    {"id": "h264", "label": "H.264", "gpu": True},
    {"id": "h265", "label": "H.265 / HEVC", "gpu": True},
    {"id": "av1", "label": "AV1", "gpu": True},
    {"id": "vp9", "label": "VP9", "gpu": False},
    {"id": "prores", "label": "ProRes", "gpu": False},
    {"id": "copy", "label": "Copy stream", "gpu": False},
]
ENCODERS = [{"id": "auto", "label": "Auto"}, {"id": "cpu", "label": "CPU"}, {"id": "gpu", "label": "GPU"}]
VIDEO_SCALES = [
    {"id": "none", "label": "Original"}, {"id": "2x", "label": "2×"}, {"id": "4x", "label": "4×"},
    {"id": "1080p", "label": "Fit 1080p"}, {"id": "720p", "label": "Fit 720p"}, {"id": "480p", "label": "Fit 480p"},
]
VIDEO_FPS = [{"id": "keep", "label": "Keep"}, {"id": "24", "label": "24"}, {"id": "30", "label": "30"},
             {"id": "60", "label": "60"}]
VIDEO_AUDIO = [{"id": "keep", "label": "Keep"}, {"id": "aac", "label": "AAC"}, {"id": "none", "label": "None"}]
VIDEO_AUDIO_BITRATES = [96, 128, 192, 256, 320]

IMAGE_FORMATS = [
    {"id": "jpg", "label": "JPEG", "lossy": True}, {"id": "png", "label": "PNG", "lossy": False},
    {"id": "webp", "label": "WebP", "lossy": True}, {"id": "avif", "label": "AVIF", "lossy": True},
    {"id": "tiff", "label": "TIFF", "lossy": False}, {"id": "bmp", "label": "BMP", "lossy": False},
    {"id": "heic", "label": "HEIC", "lossy": True},
]
IMAGE_SCALES = [
    {"id": "none", "label": "Original"}, {"id": "2x", "label": "2×"}, {"id": "4x", "label": "4×"},
    {"id": "0.5x", "label": "½"}, {"id": "0.25x", "label": "¼"},
]

AUDIO_FORMATS = [
    {"id": "mp3", "label": "MP3", "lossy": True}, {"id": "m4a", "label": "M4A · AAC", "lossy": True},
    {"id": "opus", "label": "Opus", "lossy": True}, {"id": "ogg", "label": "OGG · Vorbis", "lossy": True},
    {"id": "flac", "label": "FLAC", "lossy": False}, {"id": "wav", "label": "WAV", "lossy": False},
]
AUDIO_BITRATES = [64, 96, 128, 160, 192, 256, 320]
AUDIO_SAMPLE_RATES = [{"id": "keep", "label": "Keep"}, {"id": "44100", "label": "44.1 kHz"},
                      {"id": "48000", "label": "48 kHz"}]
AUDIO_CHANNELS = [{"id": "keep", "label": "Keep"}, {"id": "1", "label": "Mono"}, {"id": "2", "label": "Stereo"}]

UPSCALE_METHODS = [
    {"id": "lanczos", "label": "Lanczos", "hint": "Resampling with a light sharpen. Fast."},
    {"id": "fsrcnn", "label": "FSRCNN", "hint": "Small deterministic super-resolution network, bundled. Moderate."},
    {"id": "edsr", "label": "EDSR",
     "hint": "Photos only. Larger deterministic network, downloaded once (38 MB per scale). Slow, best detail."},
]
# EDSR runs at well under one frame per second on a CPU, so video offers the first two methods only.
VIDEO_UPSCALE_METHODS = UPSCALE_METHODS[:2]
UPDATE_INTERVALS = [
    {"id": "30m", "label": "Every 30 minutes"}, {"id": "1h", "label": "Every hour"},
    {"id": "6h", "label": "Every 6 hours"}, {"id": "1d", "label": "Every day"}, {"id": "1w", "label": "Every week"},
]

AUDIO_ONLY_VIDEO_FORMATS = ("mp3", "m4a", "opus", "flac", "wav")
LOSSLESS_AUDIO_FORMATS = ("flac", "wav")
LOSSY_IMAGE_FORMATS = tuple(f["id"] for f in IMAGE_FORMATS if f["lossy"])
OPUS_MAX_KBPS = 256


def _ids(table: list[dict]) -> list[str]:
    return [entry["id"] for entry in table]


def heif_supported() -> bool:
    """True when pillow-heif can be imported (HEIC read and write)."""
    try:
        import pillow_heif  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def _heif(caps: Mapping[str, Any] | None) -> bool:
    if caps is not None and "heif" in caps:
        return bool(caps.get("heif"))
    return heif_supported()


def options(caps: Mapping[str, Any] | None = None) -> dict:
    """The option tables of BRIDGE_API.md 3.7; `heic` only when HEIF is supported."""
    image_formats = [f for f in IMAGE_FORMATS if f["id"] != "heic" or _heif(caps)]
    return copy.deepcopy({
        "video": {
            "formats": VIDEO_FORMATS, "codecs": VIDEO_CODECS, "encoders": ENCODERS, "scales": VIDEO_SCALES,
            "fps": VIDEO_FPS, "audio": VIDEO_AUDIO, "audio_bitrates": VIDEO_AUDIO_BITRATES,
        },
        "image": {"formats": image_formats, "scales": IMAGE_SCALES},
        "audio": {"formats": AUDIO_FORMATS, "bitrates": AUDIO_BITRATES, "sample_rates": AUDIO_SAMPLE_RATES,
                  "channels": AUDIO_CHANNELS},
        "upscale_methods": UPSCALE_METHODS,
        "update_intervals": UPDATE_INTERVALS,
    })


# --- defaults (BRIDGE_API.md 3.3) ------------------------------------------------------------------------------

DEFAULTS: dict[str, dict[str, Any]] = {
    "video": {
        "format": "mp4", "codec": "h264", "encoder": "auto", "quality": 70, "size_limit_mb": None,
        "scale": "none", "upscale_method": "lanczos", "sharpen": True, "fps": "keep", "audio": "keep",
        "audio_bitrate_k": 192, "strip_metadata": True,
    },
    "image": {
        "format": "jpg", "quality": 85, "size_limit_mb": None, "scale": "none", "upscale_method": "lanczos",
        "sharpen": True, "strip_metadata": True,
    },
    "audio": {
        "format": "mp3", "bitrate_k": 192, "size_limit_mb": None, "sample_rate": "keep", "channels": "keep",
        "normalize": False, "strip_metadata": True,
    },
}


def defaults(kind: str) -> dict[str, Any]:
    return copy.deepcopy(DEFAULTS[kind])


# --- normalisation (BRIDGE_API.md 3.4) -------------------------------------------------------------------------

MAX_SIZE_LIMIT_MB = 1_000_000.0


def _enum(value: Any, allowed: list[str], default: str) -> str:
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip().lower()
    return text if text in allowed else default


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _int_clamp(value: Any, lo: int, hi: int, default: int) -> int:
    number = _number(value)
    if number is None:
        return default
    return int(min(hi, max(lo, round(number))))


def _nearest(value: Any, allowed: list[int], default: int) -> int:
    number = _number(value)
    if number is None:
        return default
    return min(allowed, key=lambda v: (abs(v - number), v))


def _size_limit(value: Any) -> float | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    return round(min(number, MAX_SIZE_LIMIT_MB), 3)


def _bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def nvenc_available(caps: Mapping[str, Any] | None, codec: str) -> bool:
    """Whether `codec` can use NVENC. Without probed capabilities only the static codec table is consulted."""
    static = next((c["gpu"] for c in VIDEO_CODECS if c["id"] == codec), False)
    if not static:
        return False
    if caps is None or not isinstance(caps.get("nvenc"), Mapping):
        return True
    return bool(caps["nvenc"].get(codec))


def normalize(kind: str, settings: Mapping[str, Any] | None, caps: Mapping[str, Any] | None = None) -> dict:
    """Return complete, valid settings of `kind`: unknown keys dropped, values snapped to the option tables,
    incompatible combinations resolved (BRIDGE_API.md 3.4).

    `caps` is the probed Capabilities dict, or None before the probe finished (then NVENC is assumed possible for
    the codecs that have it, so a stored `gpu` choice is not lost while probing).
    """
    if kind not in DEFAULTS:
        raise ValueError(f"unknown kind {kind!r}")
    src = dict(settings or {})
    base = DEFAULTS[kind]
    out: dict[str, Any] = {}
    if kind == "video":
        out["format"] = _enum(src.get("format"), _ids(VIDEO_FORMATS), base["format"])
        out["codec"] = _enum(src.get("codec"), _ids(VIDEO_CODECS), base["codec"])
        out["encoder"] = _enum(src.get("encoder"), _ids(ENCODERS), base["encoder"])
        out["quality"] = _int_clamp(src.get("quality"), 0, 100, base["quality"])
        out["size_limit_mb"] = _size_limit(src.get("size_limit_mb"))
        out["scale"] = _enum(src.get("scale"), _ids(VIDEO_SCALES), base["scale"])
        method = src.get("upscale_method")
        out["upscale_method"] = _enum("fsrcnn" if method == "edsr" else method, _ids(VIDEO_UPSCALE_METHODS),
                                      base["upscale_method"])
        out["sharpen"] = _bool(src.get("sharpen"), base["sharpen"])
        out["fps"] = _enum(src.get("fps"), _ids(VIDEO_FPS), base["fps"])
        out["audio"] = _enum(src.get("audio"), _ids(VIDEO_AUDIO), base["audio"])
        out["audio_bitrate_k"] = _nearest(src.get("audio_bitrate_k"), VIDEO_AUDIO_BITRATES, base["audio_bitrate_k"])
        out["strip_metadata"] = _bool(src.get("strip_metadata"), base["strip_metadata"])
        # 1. a codec not allowed for the format -> the format's first codec (codec-less formats keep the value)
        fmt = next(f for f in VIDEO_FORMATS if f["id"] == out["format"])
        if fmt["codecs"] and out["codec"] not in fmt["codecs"]:
            out["codec"] = fmt["codecs"][0]
        # 2. copy cannot scale, limit the size or change the frame rate
        if out["codec"] == "copy" and (out["scale"] != "none" or out["size_limit_mb"] is not None
                                       or out["fps"] != "keep"):
            out["codec"] = "h264"
            if fmt["codecs"] and "h264" not in fmt["codecs"]:
                out["codec"] = fmt["codecs"][0]
        # 3. gpu only for a codec with NVENC
        if out["encoder"] == "gpu" and not nvenc_available(caps, out["codec"]):
            out["encoder"] = "auto"
        return out
    if kind == "image":
        formats = [f["id"] for f in IMAGE_FORMATS if f["id"] != "heic" or _heif(caps)]
        out["format"] = _enum(src.get("format"), formats, base["format"])
        out["quality"] = _int_clamp(src.get("quality"), 0, 100, base["quality"])
        out["size_limit_mb"] = _size_limit(src.get("size_limit_mb"))
        out["scale"] = _enum(src.get("scale"), _ids(IMAGE_SCALES), base["scale"])
        out["upscale_method"] = _enum(src.get("upscale_method"), _ids(UPSCALE_METHODS), base["upscale_method"])
        out["sharpen"] = _bool(src.get("sharpen"), base["sharpen"])
        out["strip_metadata"] = _bool(src.get("strip_metadata"), base["strip_metadata"])
        return out
    out["format"] = _enum(src.get("format"), _ids(AUDIO_FORMATS), base["format"])
    out["bitrate_k"] = _nearest(src.get("bitrate_k"), AUDIO_BITRATES, base["bitrate_k"])
    out["size_limit_mb"] = _size_limit(src.get("size_limit_mb"))
    out["sample_rate"] = _enum(src.get("sample_rate"), _ids(AUDIO_SAMPLE_RATES), base["sample_rate"])
    out["channels"] = _enum(src.get("channels"), _ids(AUDIO_CHANNELS), base["channels"])
    out["normalize"] = _bool(src.get("normalize"), base["normalize"])
    out["strip_metadata"] = _bool(src.get("strip_metadata"), base["strip_metadata"])
    if out["format"] == "opus" and out["bitrate_k"] > OPUS_MAX_KBPS:
        out["bitrate_k"] = OPUS_MAX_KBPS
    return out


def merge(kind: str, current: Mapping[str, Any] | None, patch: Mapping[str, Any] | None,
          caps: Mapping[str, Any] | None = None) -> dict:
    """Apply a partial patch on top of the current settings and normalise the result."""
    combined = dict(current or {})
    combined.update(dict(patch or {}))
    return normalize(kind, combined, caps)


# --- output names (CORE.md 5.1) --------------------------------------------------------------------------------

def output_extension(kind: str, settings: Mapping[str, Any]) -> str:
    """Extension without dot for the item's output."""
    return str(settings["format"])


def is_audio_only(kind: str, settings: Mapping[str, Any]) -> bool:
    return kind == "audio" or (kind == "video" and settings.get("format") in AUDIO_ONLY_VIDEO_FORMATS)


def output_path(source: str | os.PathLike, kind: str, settings: Mapping[str, Any],
                global_settings: Mapping[str, Any], reserve: bool = False) -> Path:
    """`<dir>/<stem><suffix>.<ext>`, numbered ` (2)`, ` (3)` ... when taken; never the source itself.

    With `reserve` an empty placeholder file is created exclusively, so two jobs (or two app windows) never pick
    the same name; the job writes over it and deletes it when it fails or is cancelled.
    """
    src = Path(source)
    suffix = str(global_settings.get("suffix", "_forge"))
    if global_settings.get("output_mode") == "folder" and global_settings.get("output_dir"):
        folder = Path(str(global_settings["output_dir"]))
    else:
        folder = src.parent
    ext = output_extension(kind, settings)
    stem = src.stem + suffix
    source_key = os.path.normcase(os.path.abspath(src))
    n = 1
    while True:
        name = f"{stem}.{ext}" if n == 1 else f"{stem} ({n}).{ext}"
        candidate = folder / name
        n += 1
        if os.path.normcase(os.path.abspath(candidate)) == source_key:
            continue
        if not reserve:
            if not candidate.exists():
                return candidate
            continue
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            continue
        os.close(fd)
        return candidate
