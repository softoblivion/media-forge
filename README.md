# Media Forge

Windows desktop utility for video, photo and audio files: compress, upscale 2x / 4x, convert between formats.
One portable exe, no installer. Drop many files at once, give each its own settings, start.

## What it does

- **Compress** video (H.264, H.265, AV1, VP9; CPU or NVIDIA NVENC), photos (JPEG, WebP, AVIF, ...) and audio
  (MP3, AAC, Opus, ...), by a quality slider or by an optional size limit in MB that the output must not exceed.
- **Upscale** video and photos 2x or 4x. Default: Lanczos resampling with a light sharpen. Optional: a small
  deterministic super-resolution network (FSRCNN, bundled; EDSR, downloaded once). Nothing is generated: the same
  input always gives the same output, and no provenance marker of any kind is written.
- **Convert**: MP4, MKV, WebM, MOV (ProRes), GIF, audio extraction to MP3 / M4A / Opus / FLAC / WAV; JPEG, PNG,
  WebP, AVIF, TIFF, BMP, HEIC; MP3, M4A, Opus, OGG, FLAC, WAV.
- **Clean outputs**: by default no EXIF / XMP / IPTC, no chapters, no encoder tags, no provenance manifests. Only
  the ICC colour profile is kept.
- **Per-file settings**: every file in the list has its own panel; "Apply to all video / photos / audio" copies
  one file's settings to the others.
- **Drag and drop** of files and folders, several at once.
- **Self-update** from GitHub Releases: the app checks on launch and on the interval you set, downloads when you
  click Download (or automatically, if you turn that on) and swaps itself on Restart.

## Install

1. Download `MediaForge.exe` from the latest release and put it in any folder you own (not `Program Files`).
2. Run it. The app creates `media-forge_data\` next to itself for settings, logs, caches and models. Move or copy
   the exe together with that folder to move the whole installation.
3. Windows SmartScreen may warn about an unsigned exe: "More info" -> "Run anyway".

Requirements: Windows 10 / 11 with the Microsoft Edge WebView2 Runtime (preinstalled on Windows 11). ffmpeg is
found on `PATH` or downloaded once into `media-forge_data\bin`. An NVIDIA GPU is used for encoding when present.

## Usage

Drop files on the window or press "Choose files". Click a file to open its settings: format and codec, quality,
size limit, scale and upscale method, frame rate, audio. Press Start. Outputs are written next to the source as
`<name>_forge.<ext>` or into one folder (Settings). "Show in folder" opens the result in Explorer.

Shortcuts: `Ctrl+O` choose files, `Ctrl+Enter` start, `Delete` remove the selected file, `Esc` close a panel.

## Build from source

See `docs/BUILD.md` (Python 3.14, `build\build.ps1` produces `release\MediaForge.exe` and `SHA256SUMS`).

## Licence

GPL-3.0. See `LICENSE`.
