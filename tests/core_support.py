"""Shared helpers of the core tests: scratch folders, generated test media, a fake job context.

Every test writes below one scratch root: `%MEDIA_FORGE_HOME%` when it is set (the documented way to run the
suite), else `<temp>/media-forge-tests`. Test media is produced by ffmpeg lavfi sources and Pillow, never taken
from the user's files.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from media_forge.core import procs, tools
from media_forge.core.paths import AppPaths

_ROOT_ENV = os.environ.get("MEDIA_FORGE_HOME", "").strip()
SCRATCH = (Path(_ROOT_ENV) if _ROOT_ENV else Path(tempfile.gettempdir()) / "media-forge-tests") / "tests"
MEDIA = SCRATCH / "media"
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

needs_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg and ffprobe are not on PATH")


def _ff(*args: str) -> None:
    subprocess.run([FFMPEG, "-hide_banner", "-v", "error", "-y", *args], check=True,
                   creationflags=procs.CREATE_NO_WINDOW, stdin=subprocess.DEVNULL)


@functools.lru_cache(maxsize=None)
def nvenc() -> dict[str, bool]:
    if not FFMPEG:
        return {"h264": False, "h265": False, "av1": False}
    return {codec: tools.nvenc_test(FFMPEG, enc) for codec, enc in tools.NVENC_ENCODERS.items()}


@functools.lru_cache(maxsize=None)
def caps() -> dict:
    tool = tools.FFmpeg(FFMPEG, FFPROBE, "path") if FFMPEG and FFPROBE else None
    data = tools.probe_capabilities(tool)
    data["models"] = {"fsrcnn": "ready", "edsr": "missing"}
    return data


_media_lock = threading.Lock()


def media(name: str) -> Path:
    """Path of a generated test file (created once per scratch root)."""
    with _media_lock:
        MEDIA.mkdir(parents=True, exist_ok=True)
        path = MEDIA / name
        if path.exists() and path.stat().st_size > 0:
            return path
        _make(name, path)
        return path


def _make(name: str, path: Path) -> None:
    from PIL import Image, ImageDraw, PngImagePlugin

    if name == "clip.mp4":  # 320x240, 2 s, 30 fps, h264 + aac
        _ff("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30", "-f", "lavfi", "-i",
            "sine=frequency=440:duration=2", "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-flags:a", "+bitexact",
            "-metadata", "title=Test clip", "-metadata", "comment=source comment", "-shortest", str(path))
    elif name == "clip_long.mp4":  # 320x240, 12 s, for cancel tests
        _ff("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30", "-f", "lavfi", "-i",
            "sine=frequency=440:duration=12", "-t", "12", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-flags:a", "+bitexact",
            "-shortest", str(path))
    elif name == "clip_hq.mp4":  # 640x360, 6 s, high bitrate, for the size limit
        _ff("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30", "-f", "lavfi", "-i",
            "sine=frequency=440:duration=6", "-t", "6", "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-flags:a", "+bitexact", "-b:a", "128k", "-shortest", str(path))
    elif name == "small.mp4":  # 160x90, 1 s, for model upscales
        _ff("-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10", "-t", "1", "-c:v", "libx264", "-pix_fmt",
            "yuv420p", str(path))
    elif name == "hd.mp4":  # 1280x720, 1 s, for the fit-to-NNNp downscale
        _ff("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30", "-t", "1", "-c:v", "libx264", "-pix_fmt",
            "yuv420p", str(path))
    elif name == "odd.mp4":  # odd dimensions
        _ff("-f", "lavfi", "-i", "testsrc2=size=321x241:rate=10", "-t", "1", "-c:v", "libx264", "-pix_fmt",
            "yuv444p", str(path))
    elif name == "noaudio.mkv":
        _ff("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30", "-t", "1", "-c:v", "libx264", "-pix_fmt",
            "yuv420p", str(path))
    elif name == "audio_only.mp4":
        _ff("-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "aac", "-flags:a", "+bitexact", str(path))
    elif name == "rotated.mp4":  # 320x240 coded, displayed as 240x320
        tmp = path.with_name("rotated_src.mp4")
        _ff("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30", "-t", "1", "-c:v", "libx264", "-pix_fmt",
            "yuv420p", str(tmp))
        _ff("-display_rotation", "90", "-i", str(tmp), "-c", "copy", str(path))
    elif name == "tone.wav":
        _ff("-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:a", "pcm_s16le", str(path))
    elif name == "tone.mp3":
        _ff("-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:a", "libmp3lame", "-b:a", "192k",
            "-metadata", "title=Tone", str(path))
    elif name == "music.flac":  # 20 s noise + tone, so bitrate targets matter
        _ff("-f", "lavfi", "-i", "anoisesrc=d=20:a=0.2", "-f", "lavfi", "-i", "sine=frequency=330:duration=20",
            "-filter_complex", "[0][1]amix=inputs=2", "-ac", "2", "-c:a", "flac", str(path))
    elif name == "anim.gif":
        _ff("-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10", "-t", "1", str(path))
    elif name == "notmedia.mp4":
        path.write_bytes(b"this is not a media file\n" * 10)
    elif name in ("photo.jpg", "photo.png", "photo_exif.jpg", "single.gif", "photo.webp", "photo.bmp",
                  "alpha.png", "big.png", "gray16.png", "cmyk.jpg", "photo.tiff"):
        img = Image.new("RGB", (400, 300), (40, 60, 90))
        draw = ImageDraw.Draw(img)
        for i in range(0, 400, 20):
            draw.line([(i, 0), (400 - i, 300)], fill=(255, (i * 3) % 255, 120), width=3)
        draw.ellipse([120, 80, 280, 220], fill=(250, 200, 40))
        draw.text((10, 10), "Media Forge", fill=(255, 255, 255))
        if name == "photo.jpg":
            img.save(path, quality=92)
        elif name == "photo_exif.jpg":
            exif = Image.Exif()
            exif[0x0112] = 6  # rotate 90 cw on display
            exif[0x010F] = "TestCam"  # Make
            exif[0x0131] = "TestSoftware"
            xmp = b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF/></x:xmpmeta>'
            img.save(path, quality=92, exif=exif.tobytes(), xmp=xmp, comment=b"secret comment")
        elif name == "photo.png":
            info = PngImagePlugin.PngInfo()
            info.add_text("Comment", "png text chunk")
            img.save(path, pnginfo=info)
        elif name == "photo.webp":
            img.save(path, quality=90)
        elif name == "photo.bmp":
            img.save(path)
        elif name == "photo.tiff":
            img.save(path)
        elif name == "single.gif":
            img.convert("P").save(path)
        elif name == "alpha.png":
            rgba = img.convert("RGBA")
            alpha = Image.new("L", img.size, 0)
            ImageDraw.Draw(alpha).ellipse([50, 50, 350, 250], fill=255)
            rgba.putalpha(alpha)
            rgba.save(path)
        elif name == "big.png":  # 1600x1200 noisy image: large output at high quality
            import numpy as np
            rng = np.random.default_rng(7)
            arr = rng.integers(0, 255, (1200, 1600, 3), dtype=np.uint8)
            Image.fromarray(arr).save(path)
        elif name == "gray16.png":
            import numpy as np
            arr = (np.linspace(0, 65535, 300 * 400).reshape(300, 400)).astype(np.uint16)
            Image.fromarray(arr).save(path)
        elif name == "cmyk.jpg":
            img.convert("CMYK").save(path, quality=90)
    else:
        raise KeyError(name)


class FakeCtx:
    """A job context for calling the job functions directly."""

    def __init__(self) -> None:
        self.cancel = threading.Event()
        self.reports: list[tuple] = []

    def report(self, progress=None, stage=None, speed=None) -> None:
        self.reports.append((progress, stage, speed))

    @property
    def stages(self) -> list[str]:
        return [r[1] for r in self.reports if r[1]]


class ScratchCase(unittest.TestCase):
    """Base class: a fresh data folder (`self.paths`) and output folder (`self.out`) per test class."""

    paths: AppPaths
    out: Path

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        base = SCRATCH / cls.__name__
        if base.exists():
            shutil.rmtree(base, ignore_errors=True)
        cls.paths = AppPaths(home=base / "home")
        cls.paths.ensure()
        cls.out = base / "out"
        cls.out.mkdir(parents=True, exist_ok=True)

    def out_path(self, name: str) -> Path:
        path = self.out / name
        if path.exists():
            path.unlink()
        return path


def strings_in(path: Path, needles: tuple[bytes, ...]) -> list[bytes]:
    data = Path(path).read_bytes()
    return [n for n in needles if n in data]
