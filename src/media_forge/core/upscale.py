"""Upscaling without a generative model: Lanczos + unsharp mask, or OpenCV `dnn_superres` (FSRCNN, EDSR).

Both networks are fixed convolution stacks that map a low-resolution patch to a high-resolution patch; the same
input always gives the same output (CORE.md 4). FSRCNN ships in `media_forge/models`, EDSR is downloaded once into
`<data>/models` and verified against the SHA-256 pinned here.
"""

from __future__ import annotations

import logging
import queue
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from . import procs
from .errors import MSG_MODEL_DAMAGED, MSG_MODEL_DOWNLOAD, MSG_MODEL_MISSING, Cancelled, UserError
from .paths import AppPaths, atomic_write_json, bundled_models_dir, remove_path, replace_file
from .tools import http_download, sha256_file

log = logging.getLogger("media_forge.core.upscale")


@dataclass(frozen=True)
class ModelFile:
    name: str
    scale: int
    filename: str
    sha256: str
    size: int
    bundled: bool


MODEL_BASE_URLS = {
    "fsrcnn": "https://github.com/Saafke/FSRCNN_Tensorflow/raw/master/models/",
    "edsr": "https://github.com/Saafke/EDSR_Tensorflow/raw/master/models/",
}

MODEL_FILES: dict[tuple[str, int], ModelFile] = {
    ("fsrcnn", 2): ModelFile("fsrcnn", 2, "FSRCNN_x2.pb",
                             "366b33f0084c7b3f2bf6724f0a2c77bca94fcec9d7b6d72389d330073b380d5c", 38973, True),
    ("fsrcnn", 4): ModelFile("fsrcnn", 4, "FSRCNN_x4.pb",
                             "5c68d18db561aed8ead4ffedf1b897ea615baaf60ebf6c35f8e641f8fa4a21bf", 41661, True),
    ("edsr", 2): ModelFile("edsr", 2, "EDSR_x2.pb",
                           "585623221baa070279a0d1e7e113a4c3faba0f318ca7fdd9a65d9afc0763d9b4", 38490162, False),
    ("edsr", 4): ModelFile("edsr", 4, "EDSR_x4.pb",
                           "dd35ce3cae53ecee2d16045e08a932c3e7242d641bb65cb971d123e06904347f", 38573255, False),
}
MODEL_NAMES = ("fsrcnn", "edsr")

# Tile size (input pixels) and overlap per model; FSRCNN has a small receptive field, EDSR a large one.
TILES = {"fsrcnn": (1024, 16), "edsr": (512, 32)}

# Unsharp amounts after an upscale (CORE.md 4): Lanczos output is soft, model output is already crisp.
IMAGE_SHARPEN = {("lanczos", 2): 0.5, ("lanczos", 4): 0.35, ("model", 2): 0.25, ("model", 4): 0.18}
VIDEO_SHARPEN = {("lanczos", 2): 0.6, ("lanczos", 4): 0.45, ("model", 2): 0.3, ("model", 4): 0.22}

_verified: set[str] = set()
_verified_lock = threading.Lock()
_download_locks: dict[tuple[str, int], threading.Lock] = {key: threading.Lock() for key in MODEL_FILES}


def sharpen_amount(table: dict, method: str, factor: int) -> float:
    return table[("lanczos" if method == "lanczos" else "model", factor)]


# --- model files -----------------------------------------------------------------------------------------------

def model_path(name: str, scale: int, paths: AppPaths) -> Path:
    entry = MODEL_FILES[(name, scale)]
    return (bundled_models_dir() if entry.bundled else paths.models_dir) / entry.filename


def model_present(name: str, scale: int, paths: AppPaths) -> bool:
    path = model_path(name, scale, paths)
    return path.is_file() and path.stat().st_size == MODEL_FILES[(name, scale)].size


def model_status(paths: AppPaths) -> dict[str, str]:
    """`ready` when the files of both scales are present (size check; the hash is checked on first load)."""
    return {name: "ready" if all(model_present(name, s, paths) for s in (2, 4)) else "missing"
            for name in MODEL_NAMES}


def verify_model(name: str, scale: int, paths: AppPaths) -> Path:
    """Path of a present model file whose SHA-256 matches; a damaged downloaded file is deleted."""
    entry = MODEL_FILES[(name, scale)]
    path = model_path(name, scale, paths)
    key = str(path).lower()
    with _verified_lock:
        if key in _verified and path.is_file():
            return path
    if not path.is_file():
        raise UserError(MSG_MODEL_MISSING)
    if sha256_file(path) != entry.sha256:
        log.error("model %s has a wrong SHA-256", path)
        if not entry.bundled:
            remove_path(path)
        raise UserError(MSG_MODEL_DAMAGED)
    with _verified_lock:
        _verified.add(key)
    return path


def ensure_model(name: str, scale: int, paths: AppPaths,
                 on_progress: Callable[[int, int | None], None] | None = None,
                 cancel: threading.Event | None = None) -> Path:
    """The verified model file; a missing EDSR file is downloaded (`.part`, SHA-256, replace, models.json)."""
    entry = MODEL_FILES[(name, scale)]
    if entry.bundled or model_present(name, scale, paths):
        return verify_model(name, scale, paths)
    with _download_locks[(name, scale)]:
        if model_present(name, scale, paths):
            return verify_model(name, scale, paths)
        target = model_path(name, scale, paths)
        part = target.with_name(target.name + ".part")
        url = MODEL_BASE_URLS[name] + entry.filename
        try:
            got = http_download(url, part, on_progress, cancel)
        except Cancelled:
            remove_path(part)
            raise
        except Exception as exc:  # noqa: BLE001  network, disk
            remove_path(part)
            log.warning("model download failed: %s", exc)
            raise UserError(MSG_MODEL_DOWNLOAD) from exc
        if got != entry.sha256:
            remove_path(part)
            log.error("downloaded %s has SHA-256 %s, expected %s", entry.filename, got, entry.sha256)
            raise UserError(MSG_MODEL_DAMAGED)
        replace_file(part, target)
        _record_model(paths, entry, url)
        with _verified_lock:
            _verified.add(str(target).lower())
        return target


def _record_model(paths: AppPaths, entry: ModelFile, url: str) -> None:
    import json
    data: dict = {}
    try:
        data = json.loads(paths.models_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data[entry.filename] = {"name": entry.name, "scale": entry.scale, "source": url, "sha256": entry.sha256,
                            "size": entry.size}
    try:
        atomic_write_json(paths.models_file, data)
    except OSError:
        log.exception("models.json could not be written")


# --- resampling ------------------------------------------------------------------------------------------------

def lanczos(img: np.ndarray, factor: float) -> np.ndarray:
    """Resize by `factor` with OpenCV's Lanczos (8x8 kernel); any channel count, uint8 or uint16."""
    h, w = img.shape[:2]
    size = (max(1, int(round(w * factor))), max(1, int(round(h * factor))))
    out = cv2.resize(img, size, interpolation=cv2.INTER_LANCZOS4)
    if img.ndim == 3 and out.ndim == 2:
        out = out[:, :, None]
    return out


def sharpen(img: np.ndarray, amount: float) -> np.ndarray:
    """Unsharp mask: `img * (1 + a) - blur * a`, blur = Gaussian with sigma 1.0."""
    if amount <= 0:
        return img
    blur = cv2.GaussianBlur(img, (0, 0), 1.0)
    out = cv2.addWeighted(img, 1.0 + amount, blur, -amount, 0)
    if img.ndim == 3 and out.ndim == 2:
        out = out[:, :, None]
    return out


class Model:
    """One loaded `dnn_superres` network. Not shared between threads; each job loads its own."""

    def __init__(self, name: str, scale: int, path: Path) -> None:
        self.name = name
        self.scale = scale
        self.sr = cv2.dnn_superres.DnnSuperResImpl.create()
        self.sr.readModel(str(path))
        self.sr.setModel(name, scale)
        self.tile, self.overlap = TILES[name]

    @classmethod
    def load(cls, name: str, scale: int, paths: AppPaths) -> "Model":
        return cls(name, scale, verify_model(name, scale, paths))

    def upsample(self, bgr: np.ndarray) -> np.ndarray:
        """Upscale a uint8 BGR image; frames larger than one tile are processed in overlapping tiles."""
        h, w = bgr.shape[:2]
        if h <= self.tile and w <= self.tile:
            return self.sr.upsample(np.ascontiguousarray(bgr))
        s, t, ov = self.scale, self.tile, self.overlap
        out = np.empty((h * s, w * s, 3), dtype=np.uint8)
        for y0 in range(0, h, t):
            y1 = min(h, y0 + t)
            iy0, iy1 = max(0, y0 - ov), min(h, y1 + ov)
            for x0 in range(0, w, t):
                x1 = min(w, x0 + t)
                ix0, ix1 = max(0, x0 - ov), min(w, x1 + ov)
                tile = self.sr.upsample(np.ascontiguousarray(bgr[iy0:iy1, ix0:ix1]))
                oy, ox = (y0 - iy0) * s, (x0 - ix0) * s
                out[y0 * s:y1 * s, x0 * s:x1 * s] = tile[oy:oy + (y1 - y0) * s, ox:ox + (x1 - x0) * s]
        return out


def upscale_array(img: np.ndarray, factor: int, method: str, sharpen_on: bool, model: Model | None = None,
                  sharpen_table: dict = IMAGE_SHARPEN) -> np.ndarray:
    """Upscale an RGB / RGBA / grey uint8 array (HxWxC, C in 1, 3, 4) by 2 or 4.

    Models work on the colour channels (converted to BGR); the alpha channel is resized with Lanczos and
    re-attached. The sharpen pass touches the colour channels only.
    """
    if img.ndim == 2:
        img = img[:, :, None]
    channels = img.shape[2]
    color = img[:, :, :3] if channels >= 3 else img[:, :, :1]
    alpha = img[:, :, 3:4] if channels == 4 else None
    if method == "lanczos" or model is None or img.dtype != np.uint8:
        up = lanczos(color, factor)
    else:
        if color.shape[2] == 1:
            bgr = cv2.cvtColor(color[:, :, 0], cv2.COLOR_GRAY2BGR)
            up = model.upsample(bgr)
            up = cv2.cvtColor(up, cv2.COLOR_BGR2GRAY)[:, :, None]
        else:
            up = model.upsample(cv2.cvtColor(color, cv2.COLOR_RGB2BGR))
            up = cv2.cvtColor(up, cv2.COLOR_BGR2RGB)
    if sharpen_on:
        up = sharpen(up, sharpen_amount(sharpen_table, method, factor))
    if alpha is not None:
        a = lanczos(alpha, factor)
        up = np.concatenate([up, a], axis=2)
    return up


# --- video frame pipeline (CORE.md 3.4) ------------------------------------------------------------------------

@dataclass
class PipelineResult:
    frames: int
    decoder_rc: int | None
    encoder_rc: int | None
    decoder_tail: list[str]
    encoder_tail: list[str]
    cancelled: bool = False
    write_failed: bool = False


def frame_pipeline(decoder_args: list[str], encoder_args: list[str], width: int, height: int,
                   transform: Callable[[np.ndarray], np.ndarray], *, cancel: threading.Event | None = None,
                   on_frame: Callable[[int], None] | None = None) -> PipelineResult:
    """decoder (rawvideo bgr24 on stdout) -> transform per frame -> encoder (rawvideo bgr24 on stdin).

    A reader thread and a writer thread keep both processes busy while the main thread runs the model; at most
    a few frames are in memory. Both processes live in one Job Object, so cancel kills both trees.
    """
    frame_bytes = width * height * 3
    job = procs.JobObject()
    dec = enc = None
    frames = 0
    cancelled = write_failed = False
    in_q: queue.Queue = queue.Queue(maxsize=3)
    out_q: queue.Queue = queue.Queue(maxsize=3)
    stop = threading.Event()
    try:
        dec = procs.spawn(decoder_args, job=job, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE)
        dec_err = procs.LinePump(dec.stderr)  # type: ignore[arg-type]
        enc = procs.spawn(encoder_args, job=job, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                          stderr=subprocess.PIPE)
        enc_err = procs.LinePump(enc.stderr)  # type: ignore[arg-type]

        def reader() -> None:
            stream = dec.stdout
            try:
                while not stop.is_set():
                    buf = bytearray(frame_bytes)
                    view = memoryview(buf)
                    got = 0
                    while got < frame_bytes:
                        n = stream.readinto(view[got:])  # type: ignore[union-attr]
                        if not n:
                            break
                        got += n
                    if got < frame_bytes:
                        break
                    while not stop.is_set():
                        try:
                            in_q.put(buf, timeout=0.2)
                            break
                        except queue.Full:
                            continue
            except (OSError, ValueError):
                pass
            finally:
                while not stop.is_set():
                    try:
                        in_q.put(None, timeout=0.2)
                        break
                    except queue.Full:
                        continue

        failed = threading.Event()

        def writer() -> None:
            try:
                while True:
                    data = out_q.get()
                    if data is None:
                        break
                    if failed.is_set():
                        continue
                    try:
                        enc.stdin.write(data)  # type: ignore[union-attr]
                    except (OSError, ValueError):
                        failed.set()
            finally:
                try:
                    enc.stdin.close()  # type: ignore[union-attr]
                except OSError:
                    pass

        t_read = threading.Thread(target=reader, daemon=True)
        t_write = threading.Thread(target=writer, daemon=True)
        t_read.start()
        t_write.start()
        while True:
            if cancel is not None and cancel.is_set():
                cancelled = True
                break
            if failed.is_set():
                write_failed = True
                break
            try:
                buf = in_q.get(timeout=0.2)
            except queue.Empty:
                continue
            if buf is None:
                break
            frame = np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 3)
            data = np.ascontiguousarray(transform(frame)).tobytes()
            while True:
                try:
                    out_q.put(data, timeout=0.2)
                    break
                except queue.Full:
                    if (cancel is not None and cancel.is_set()) or failed.is_set():
                        break
            if cancel is not None and cancel.is_set():
                cancelled = True
                break
            frames += 1
            if on_frame is not None:
                on_frame(frames)
        stop.set()
        if cancelled or write_failed:
            job.kill()
        out_q.put(None)
        t_write.join(timeout=60 if not cancelled else 5)
        try:
            dec.wait(timeout=30)
        except subprocess.TimeoutExpired:
            job.kill()
        try:
            enc.wait(timeout=600 if not cancelled else 10)
        except subprocess.TimeoutExpired:
            job.kill()
        t_read.join(timeout=5)
        try:
            dec.stdout.close()  # type: ignore[union-attr]
        except OSError:
            pass
        dec_err.join(timeout=5)
        enc_err.join(timeout=5)
        if failed.is_set():
            write_failed = True
        return PipelineResult(frames, dec.returncode, enc.returncode, dec_err.lines(), enc_err.lines(),
                              cancelled, write_failed)
    except BaseException:
        job.kill()
        raise
    finally:
        job.close()
