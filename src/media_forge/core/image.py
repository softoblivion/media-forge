"""Image jobs: Pillow pipeline with optional upscale, metadata stripping and a size cap (CORE.md 5.2)."""

from __future__ import annotations

import io
import logging
import math
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np
from PIL import Image, ImageCms, ImageOps

from . import catalog
from .errors import MSG_CANNOT_LIMIT, MSG_LIMIT_TOO_SMALL_IMAGE, MSG_NOT_MEDIA, Cancelled, UserError
from .probe import ProbeInfo, pillow_heif, to_8bit_gray
from .upscale import IMAGE_SHARPEN, Model, upscale_array

log = logging.getLogger("media_forge.core.image")

ALPHA_FORMATS = ("png", "webp", "avif", "tiff")
GRAY_FORMATS = ("jpg", "png", "tiff", "bmp")
SIXTEEN_BIT_FORMATS = ("png", "tiff")
PIL_FORMATS = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP", "avif": "AVIF", "tiff": "TIFF", "bmp": "BMP",
               "heic": "HEIF"}
EXIF_FORMATS = ("jpg", "png", "webp", "avif", "tiff", "heic")
XMP_FORMATS = ("jpg", "webp", "avif")
DOWNSCALES = {"0.5x": 0.5, "0.25x": 0.25}
UPSCALES = {"2x": 2, "4x": 4}
SEARCH_STEPS = 7
LADDER_RUNGS = 4
MIN_SHORT_SIDE = 64
MIN_QUALITY = 10

ModelGetter = Callable[[str, int], Model]


def _check(ctx) -> None:
    if ctx.cancel.is_set():
        raise Cancelled()


def _composite_white(img: Image.Image) -> Image.Image:
    rgba = img.convert("RGBA")
    base = Image.new("RGB", rgba.size, (255, 255, 255))
    base.paste(rgba, mask=rgba.getchannel("A"))
    return base


def prepare(img: Image.Image, fmt: str, keep_16bit: bool) -> tuple[Image.Image, bool]:
    """Convert to a mode the target format stores; returns (image, icc_still_valid)."""
    mode = img.mode
    icc_ok = True
    has_transparency = "transparency" in img.info
    if mode in ("I;16", "I;16B", "I;16L", "I", "F"):
        if keep_16bit and fmt in SIXTEEN_BIT_FORMATS and mode != "F":
            arr = np.asarray(img)
            if arr.dtype != np.uint16:
                arr = np.clip(arr, 0, 65535).astype(np.uint16)
            img = Image.fromarray(arr)
            return img, icc_ok
        img = to_8bit_gray(img)
        mode = "L"
    if mode == "1":
        img, mode = img.convert("L"), "L"
    if mode == "CMYK":
        icc = img.info.get("icc_profile")
        if icc:
            try:
                src_profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
                img = ImageCms.profileToProfile(img, src_profile, ImageCms.createProfile("sRGB"), outputMode="RGB")
            except Exception:  # noqa: BLE001
                img = img.convert("RGB")
        else:
            img = img.convert("RGB")
        mode, icc_ok = "RGB", False
    if mode in ("P", "PA"):
        img = img.convert("RGBA" if (has_transparency or mode == "PA") else "RGB")
        mode = img.mode
    elif mode == "LA" or (mode == "L" and has_transparency):
        img, mode, icc_ok = img.convert("RGBA"), "RGBA", False
    elif mode == "RGB" and has_transparency:
        img, mode = img.convert("RGBA"), "RGBA"
    elif mode not in ("L", "RGB", "RGBA"):
        if mode == "LAB":
            icc_ok = False
        img, mode = img.convert("RGB"), "RGB"
    if mode == "RGBA" and fmt not in ALPHA_FORMATS:
        img, mode = _composite_white(img), "RGB"
    if mode == "L" and fmt not in GRAY_FORMATS:
        img, mode, icc_ok = img.convert("RGB"), "RGB", False
    return img, icc_ok


def save_params(fmt: str, quality: int, icc: bytes | None, exif: bytes | None, xmp: bytes | None) -> dict:
    params: dict[str, Any] = {"format": PIL_FORMATS[fmt]}
    if fmt == "jpg":
        params.update(quality=quality, optimize=True, progressive=True, subsampling=0 if quality >= 90 else 2)
    elif fmt == "png":
        params.update(optimize=True, compress_level=9)
    elif fmt == "webp":
        params.update(quality=quality, method=6)
    elif fmt == "avif":
        params.update(quality=quality, speed=4)
    elif fmt == "tiff":
        params.update(compression="tiff_deflate")
    elif fmt == "heic":
        params.update(quality=quality)
    if icc and fmt != "bmp":
        params["icc_profile"] = icc
    if exif and fmt in EXIF_FORMATS:
        params["exif"] = exif
    if xmp and fmt in XMP_FORMATS:
        params["xmp"] = xmp
    return params


def encode(img: Image.Image, fmt: str, quality: int, icc: bytes | None, exif: bytes | None,
           xmp: bytes | None) -> bytes:
    buf = io.BytesIO()
    clean = img.copy()
    clean.info = {}  # nothing of the source rides along implicitly (text chunks, comments, dpi, exif)
    params = save_params(fmt, quality, icc, exif, xmp)
    try:
        clean.save(buf, **params)
    except OSError:
        if fmt != "jpg":
            raise
        # Pillow buffers an optimized / progressive JPEG in about width x height bytes; extremely detailed images
        # at high quality do not fit, so they are written as baseline JPEG without Huffman optimisation.
        buf = io.BytesIO()
        params.update(optimize=False, progressive=False)
        clean.save(buf, **params)
    return buf.getvalue()


def _search_quality(img: Image.Image, fmt: str, top: int, cap: int, enc: Callable[[Image.Image, int], bytes],
                    ctx) -> tuple[bytes | None, int]:
    """Largest quality in [10, top] whose output fits `cap`: (bytes or None, size at the lowest quality)."""
    lo_q = min(MIN_QUALITY, top)
    low = enc(img, lo_q)
    if len(low) > cap:
        return None, len(low)
    best, lo, hi = low, lo_q + 1, top
    steps = 1
    while lo <= hi and steps < SEARCH_STEPS:
        _check(ctx)
        mid = (lo + hi + 1) // 2
        data = enc(img, mid)
        steps += 1
        if len(data) <= cap:
            best, lo = data, mid + 1
        else:
            hi = mid - 1
    return best, len(low)


def run_image(src: Path, info: ProbeInfo, settings: Mapping[str, Any], out: Path, ctx,
              get_model: ModelGetter | None = None) -> dict:
    """Convert one image; writes `out` and returns {"width", "height"} of the result."""
    fmt = settings["format"]
    quality = int(settings["quality"])
    strip = bool(settings["strip_metadata"])
    scale = settings["scale"]
    factor = UPSCALES.get(scale)
    method = settings["upscale_method"] if factor else "lanczos"
    ctx.report(progress=0.05, stage="analyzing")
    try:
        with Image.open(src) as opened:
            opened.load()
            icc = opened.info.get("icc_profile")
            xmp = opened.info.get("xmp") or opened.info.get("XML:com.adobe.xmp")
            img = ImageOps.exif_transpose(opened)
            exif_raw = img.info.get("exif")
    except UserError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("cannot open %s: %s", src, exc)
        raise UserError(MSG_NOT_MEDIA) from exc
    if isinstance(xmp, str):
        xmp = xmp.encode("utf-8")
    _check(ctx)
    ctx.report(progress=0.1, stage="analyzing")
    model_upscale = bool(factor) and method in ("fsrcnn", "edsr")
    img, icc_ok = prepare(img, fmt, keep_16bit=not model_upscale)
    if not icc_ok:
        icc = None

    if scale in DOWNSCALES:
        f = DOWNSCALES[scale]
        size = (max(1, int(round(img.width * f))), max(1, int(round(img.height * f))))
        img = img.resize(size, Image.LANCZOS, reducing_gap=3.0)
    elif factor:
        model = None
        if model_upscale:
            if get_model is None:
                raise RuntimeError("a model getter is required for a model upscale")
            model = get_model(method, factor)
            _check(ctx)
        ctx.report(progress=0.3, stage="upscaling")
        arr = np.asarray(img)
        up = upscale_array(arr, factor, method, bool(settings["sharpen"]), model, IMAGE_SHARPEN)
        if up.ndim == 3 and up.shape[2] == 1:
            up = up[:, :, 0]
        img = Image.fromarray(np.ascontiguousarray(up))
        ctx.report(progress=0.8, stage="upscaling")
    _check(ctx)
    ctx.report(progress=0.9, stage="writing")

    exif = exif_raw if not strip else None
    xmp_out = xmp if not strip else None

    def enc(image: Image.Image, q: int) -> bytes:
        return encode(image, fmt, q, icc, exif, xmp_out)

    limit = settings.get("size_limit_mb")
    data = enc(img, quality)
    if limit is not None:
        cap = int(float(limit) * 1024 * 1024)
        if len(data) > cap:
            if fmt not in catalog.LOSSY_IMAGE_FORMATS:
                raise UserError(MSG_CANNOT_LIMIT)
            result, low_size = _search_quality(img, fmt, quality, cap, enc, ctx)
            rung = 0
            while result is None:
                if rung >= LADDER_RUNGS:
                    raise UserError(MSG_LIMIT_TOO_SMALL_IMAGE)
                short = min(img.width, img.height)
                if short <= MIN_SHORT_SIDE:
                    raise UserError(MSG_LIMIT_TOO_SMALL_IMAGE)
                f = math.sqrt(cap / low_size) * 0.95
                f = max(f, MIN_SHORT_SIDE / short)
                size = (max(1, int(round(img.width * f))), max(1, int(round(img.height * f))))
                img = img.resize(size, Image.LANCZOS, reducing_gap=3.0)
                rung += 1
                log.info("size cap: downscaled to %dx%d (rung %d)", img.width, img.height, rung)
                result, low_size = _search_quality(img, fmt, quality, cap, enc, ctx)
            data = result
    _check(ctx)
    with open(out, "wb") as handle:
        handle.write(data)
    ctx.report(progress=1.0, stage="writing")
    return {"width": img.width, "height": img.height}


__all__ = ["run_image", "prepare", "encode", "pillow_heif"]
