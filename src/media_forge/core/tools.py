"""ffmpeg / ffprobe discovery and download, verified HTTP downloads, capability probe (BRIDGE_API.md 3.6).

Discovery order: `<data>/bin` first, then PATH. When neither has both tools, the static build of
yt-dlp/FFmpeg-Builds is downloaded once, verified against the release's `checksums.sha256`, and the two exes are
extracted through `.part` files.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import threading
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import procs
from .errors import Cancelled
from .paths import AppPaths, atomic_write_json, remove_path, replace_file

log = logging.getLogger("media_forge.core.tools")

FFMPEG_BASE = "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/"
FFMPEG_ZIP = "ffmpeg-master-latest-win64-gpl.zip"
FFMPEG_SUMS = "checksums.sha256"
USER_AGENT = "MediaForge/1 (Windows)"
NVENC_ENCODERS = {"h264": "h264_nvenc", "h265": "hevc_nvenc", "av1": "av1_nvenc"}
NVENC_TIMEOUT = 10.0

ProgressFn = Callable[[int, int | None], None]


class DownloadError(Exception):
    pass


@dataclass
class FFmpeg:
    ffmpeg: str
    ffprobe: str
    source: str  # "bin" | "path"


def find_ffmpeg(paths: AppPaths, use_path: bool = True) -> FFmpeg | None:
    """`bin/ffmpeg.exe` + `bin/ffprobe.exe`, else both from PATH; None when either is missing."""
    own_ff, own_fp = paths.binary("ffmpeg.exe"), paths.binary("ffprobe.exe")
    if own_ff.is_file() and own_fp.is_file():
        return FFmpeg(str(own_ff), str(own_fp), "bin")
    if use_path:
        ff, fp = shutil.which("ffmpeg"), shutil.which("ffprobe")
        if ff and fp:
            return FFmpeg(ff, fp, "path")
    return None


# --- downloads -------------------------------------------------------------------------------------------------

def http_get_text(url: str, timeout: float = 30.0, limit: int = 1 << 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(limit).decode("utf-8", errors="replace")


def http_download(url: str, dest: Path, on_progress: ProgressFn | None = None,
                  cancel: threading.Event | None = None, timeout: float = 60.0) -> str:
    """Download `url` into `dest` (the caller passes a `.part` path); returns the SHA-256 hex of the bytes."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    done = 0
    last = 0.0
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as out:
        total_raw = resp.headers.get("Content-Length")
        total = int(total_raw) if total_raw and total_raw.isdigit() else None
        while True:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            chunk = resp.read(1 << 17)
            if not chunk:
                break
            out.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            now = time.monotonic()
            if on_progress is not None and now - last >= 0.15:
                last = now
                on_progress(done, total)
        out.flush()
        os.fsync(out.fileno())
    if on_progress is not None:
        on_progress(done, done)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_sums(text: str) -> dict[str, str]:
    """`<sha256>  <name>` lines (sha256sum format, optional `*` before the name)."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
        if match:
            result[match.group(2)] = match.group(1).lower()
    return result


def extract_ffmpeg(zip_path: Path, bin_dir: Path) -> dict[str, Path]:
    """Extract `bin/ffmpeg.exe` and `bin/ffprobe.exe` of a release zip into `bin_dir` through `.part` files."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    extracted: dict[str, Path] = {}
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.infolist():
                norm = "/" + member.filename.replace("\\", "/")
                base = norm.rsplit("/", 1)[-1].lower()
                if base in ("ffmpeg.exe", "ffprobe.exe") and "/bin/" in norm:
                    part = bin_dir / (base + ".part")
                    with zf.open(member) as src, open(part, "wb") as dst:
                        shutil.copyfileobj(src, dst, 1 << 20)
                    extracted[base] = part
        if set(extracted) != {"ffmpeg.exe", "ffprobe.exe"}:
            raise DownloadError("the ffmpeg archive does not contain bin/ffmpeg.exe and bin/ffprobe.exe")
        final: dict[str, Path] = {}
        for base, part in extracted.items():
            target = bin_dir / base
            replace_file(part, target)
            final[base] = target
        return final
    finally:
        for part in extracted.values():
            remove_path(part)


def download_ffmpeg(paths: AppPaths, on_progress: ProgressFn | None = None,
                    cancel: threading.Event | None = None, base_url: str = FFMPEG_BASE) -> FFmpeg:
    """Download, verify and install the static ffmpeg build into `<data>/bin`; records it in `versions.json`."""
    sums = parse_sums(http_get_text(base_url + FFMPEG_SUMS))
    expected = sums.get(FFMPEG_ZIP)
    if not expected:
        raise DownloadError(f"{FFMPEG_ZIP} is not listed in {FFMPEG_SUMS}")
    paths.cache_dir.mkdir(parents=True, exist_ok=True)
    part = paths.cache_dir / (FFMPEG_ZIP + ".part")
    try:
        got = http_download(base_url + FFMPEG_ZIP, part, on_progress, cancel)
        if got != expected:
            raise DownloadError(f"SHA-256 mismatch of {FFMPEG_ZIP}")
        exes = extract_ffmpeg(part, paths.bin_dir)
    finally:
        remove_path(part)
    version = ffmpeg_version(str(exes["ffmpeg.exe"]))
    atomic_write_json(paths.versions_file, {"ffmpeg": {
        "version": version, "source": base_url + FFMPEG_ZIP, "zip_sha256": expected,
        "ffmpeg_sha256": sha256_file(exes["ffmpeg.exe"]), "ffprobe_sha256": sha256_file(exes["ffprobe.exe"]),
        "installed_at": time.time()}})
    return FFmpeg(str(exes["ffmpeg.exe"]), str(exes["ffprobe.exe"]), "bin")


# --- capabilities ----------------------------------------------------------------------------------------------

def parse_ffmpeg_version(text: str) -> str | None:
    match = re.search(r"ffmpeg version (\S+)", text)
    if not match:
        return None
    raw = match.group(1)
    semver = re.match(r"n?(\d+(?:\.\d+)+)", raw)
    if semver:
        return semver.group(1)
    nightly = re.match(r"(N-\d+)", raw)
    return nightly.group(1) if nightly else raw


def ffmpeg_version(ffmpeg: str) -> str | None:
    res = procs.run([ffmpeg, "-hide_banner", "-version"], capture=True, timeout=15)
    if res.returncode != 0:
        return None
    return parse_ffmpeg_version(res.stdout.decode("utf-8", errors="replace"))


def list_encoders(ffmpeg: str) -> set[str]:
    res = procs.run([ffmpeg, "-hide_banner", "-encoders"], capture=True, timeout=15)
    names: set[str] = set()
    for line in res.stdout.decode("utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) >= 2 and re.fullmatch(r"[VAS][F.][S.][X.][B.][D.]", parts[0]):
            names.add(parts[1])
    return names


def nvenc_test(ffmpeg: str, encoder: str) -> bool:
    """One-frame test encode; True when the driver opened an encode session."""
    res = procs.run([ffmpeg, "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "color=size=256x256:rate=1",
                     "-frames:v", "1", "-pix_fmt", "yuv420p", "-c:v", encoder, "-f", "null", "-"],
                    timeout=NVENC_TIMEOUT)
    return res.returncode == 0 and not res.timed_out


def gpu_name() -> str | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        candidate = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe"
        exe = str(candidate) if candidate.is_file() else None
    if not exe:
        return None
    res = procs.run([exe, "--query-gpu=name", "--format=csv,noheader"], capture=True, timeout=10)
    if res.returncode != 0:
        return None
    lines = [ln.strip() for ln in res.stdout.decode("utf-8", errors="replace").splitlines() if ln.strip()]
    return lines[0] if lines else None


def avif_supported() -> bool:
    try:
        from PIL import features
        return bool(features.check("avif"))
    except Exception:  # noqa: BLE001
        return False


def probe_capabilities(tool: FFmpeg | None) -> dict:
    """Encoders, NVENC per codec (parallel one-frame tests), GPU name, AVIF / HEIF. Models are added by the caller."""
    from .catalog import heif_supported

    caps: dict = {
        "ffmpeg": "ready" if tool else "missing",
        "ffmpeg_version": None,
        "ffmpeg_path": tool.ffmpeg if tool else None,
        "nvenc": {codec: False for codec in NVENC_ENCODERS},
        "gpu_name": None,
        "avif": avif_supported(),
        "heif": heif_supported(),
        "encoders": [],
    }
    if tool is None:
        return caps
    caps["ffmpeg_version"] = ffmpeg_version(tool.ffmpeg)
    encoders = list_encoders(tool.ffmpeg)
    caps["encoders"] = sorted(encoders)
    wanted = {codec: enc for codec, enc in NVENC_ENCODERS.items() if enc in encoders}
    if wanted:
        with ThreadPoolExecutor(max_workers=len(wanted) + 1) as pool:
            futures = {codec: pool.submit(nvenc_test, tool.ffmpeg, enc) for codec, enc in wanted.items()}
            name_future = pool.submit(gpu_name)
            for codec, future in futures.items():
                caps["nvenc"][codec] = bool(future.result())
            caps["gpu_name"] = name_future.result()
    return caps


def public_capabilities(caps: dict) -> dict:
    """The Capabilities payload of BRIDGE_API.md 3.6 (internal keys such as `encoders` removed)."""
    keys = ("ffmpeg", "ffmpeg_version", "ffmpeg_path", "nvenc", "gpu_name", "avif", "heif", "models")
    return {key: caps.get(key) for key in keys}
