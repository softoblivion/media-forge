# 0001 — Deterministic upscaling and metadata-free outputs

Date: 2026-10-21

## Context

The app must upscale video and photos 2x / 4x, and the results must never be flagged as generated media by
platforms that read provenance metadata (C2PA manifests, IPTC `DigitalSourceType`) or by heuristics on encoder
tags. Generative upscalers (GAN or diffusion based, e.g. Real-ESRGAN, SUPIR) invent texture, differ between runs,
and some toolchains attach provenance manifests to their outputs.

## Decision

1. Upscaling is resampling or deterministic regression only: Lanczos (default, with a light unsharp mask), or a
   fixed-weight convolutional super-resolution network through OpenCV `dnn_superres` (FSRCNN bundled, EDSR
   downloaded on demand). These models map a low-resolution patch to a high-resolution one by fixed convolutions:
   same input, same output, no sampling, no prompt, no noise. No generative model is ever added to this project.
2. Every output is written without metadata by default: ffmpeg `-map_metadata -1 -map_chapters -1` plus bit-exact
   flags (no `encoder` / `Lavf` tags), Pillow saves without EXIF / XMP / IPTC / text chunks. Only the ICC profile
   is kept, since it affects colour. The app has no code path that writes a provenance manifest.
3. "Strip metadata" is a per-file switch, default on; off copies the source metadata as far as the format allows.

## Consequences

- Upscaled detail is limited to what resampling or a small regression network can restore; the app never
  invents detail, which is the point.
- EDSR runs on the CPU (no CUDA build of OpenCV): slow for long videos; the UI shows the measured speed and ETA.
- Rejected: Real-ESRGAN / ncnn-vulkan (GAN, generative texture), diffusion upscalers (generative), ffmpeg's
  `sr` filter (needs a TensorFlow build), `libplacebo` shaders (GPU-only, Vulkan dependency for a 12 MB exe).
