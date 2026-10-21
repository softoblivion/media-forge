<div align="center">

<img src="src/media_forge/ui/assets/icon.svg" alt="" width="96" height="96">

# Media Forge: compress, upscale and convert video, photos and audio on Windows

**Drop files, give each its own settings, press Start. Compress to a quality or to a size limit in MB, upscale 2x / 4x without inventing pixels, convert between the common video, image and audio formats. Outputs carry no metadata. One portable exe, no installer, no account; updates itself from GitHub Releases.**

[![Download](https://img.shields.io/badge/Download-MediaForge.exe-4d8dff?style=for-the-badge&logo=windows&logoColor=white)](https://github.com/softoblivion/media-forge/releases/latest/download/MediaForge.exe)
[![Release](https://img.shields.io/github/v/release/softoblivion/media-forge?display_name=release&label=latest&style=for-the-badge)](https://github.com/softoblivion/media-forge/releases/latest)

![Platform](https://img.shields.io/badge/platform-Windows_10_|_11-0078D6?logo=windows&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.14-3776AB?logo=python&logoColor=white)
![JavaScript](https://img.shields.io/badge/JavaScript-ES_modules-F7DF1E?logo=javascript&logoColor=black)
![HTML5](https://img.shields.io/badge/HTML5-E34F26?logo=html5&logoColor=white)
![CSS](https://img.shields.io/badge/CSS-663399?logo=css&logoColor=white)
![FFmpeg](https://img.shields.io/badge/FFmpeg-007808?logo=ffmpeg&logoColor=white)
![OpenCV](https://img.shields.io/badge/OpenCV-5-5C3EE8?logo=opencv&logoColor=white)
![PowerShell](https://img.shields.io/badge/PowerShell-5391FE?logo=powershell&logoColor=white)
[![License](https://img.shields.io/badge/license-GPL--3.0-blue)](LICENSE)

<img src="docs/assets/screenshot.png" alt="Media Forge main window with three queued files" width="42%">
<img src="docs/assets/settings.png" alt="Per-file settings of a video" width="42%">

<sub>Drop files on the window · open a file's settings · press Start. Outputs land next to the sources.</sub>

</div>

---

## Contents

- [Why Media Forge](#why-media-forge)
- [Quick start](#quick-start)
- [Features](#features)
- [Usage](#usage)
- [Settings](#settings)
- [Formats and codecs](#formats-and-codecs)
- [Size limit](#size-limit)
- [Data and privacy](#data-and-privacy)
- [Updates](#updates)
- [Building from source](#building-from-source)
- [Project structure](#project-structure)
- [Third-party components](#third-party-components)
- [License](#license)

## Why Media Forge

- **Three jobs, one window.** Compress, upscale and convert are the same panel: pick the output format, the
  codec, the quality or a size limit, the scale. Every file in the list keeps its own settings; one click copies
  them to the other files of the same kind.
- **A size limit that holds.** Type a number of MB and the output stays under it: two-pass bitrate targeting for
  video, a quality search and then a downscale ladder for photos, a bitrate from the duration for audio.
- **Upscale without invention.** 2x / 4x by Lanczos resampling with a light sharpen, or by a small deterministic
  super-resolution network (FSRCNN bundled, EDSR for photos, downloaded once). The same input always gives the
  same output; nothing is painted in from noise or text.
- **Clean outputs.** By default no EXIF, XMP, IPTC, chapters, encoder tags or provenance manifests of any kind.
  Only the ICC colour profile is kept.
- **Free and open source.** GPL-3.0, no trial, no watermark, no paid tier. The source is here; build it yourself
  if you prefer.
- **Portable.** One `MediaForge.exe` with a data folder next to it. Move the folder, move the app.

## Quick start

1. Download [`MediaForge.exe`](https://github.com/softoblivion/media-forge/releases/latest/download/MediaForge.exe)
   into a folder of its own, for example `D:\Apps\MediaForge\`.
2. Run it. FFmpeg is taken from `PATH` or downloaded once into the data folder; an NVIDIA GPU is used for encoding
   when the machine has one.
3. Drop files or folders on the window (or press `Ctrl+O`), open a file's settings if the defaults are not what
   you want, press **Start**. Outputs are written next to the sources as `<name>_forge.<ext>`.

**Requirements:** Windows 10 or 11 with the Microsoft Edge WebView2 Runtime (preinstalled on Windows 11 and on
up-to-date Windows 10). No installer, no admin rights.

> [!NOTE]
> The executable is not code-signed, so Windows SmartScreen may warn on first start (**More info → Run anyway**).

## Features

| | |
|---|---|
| **Compress video** | H.264, H.265 / HEVC, AV1 (CPU, or NVIDIA NVENC when present), VP9 and ProRes. A quality slider (100 = visually lossless) or a size limit in MB. Fit to 1080p / 720p / 480p, frame rate 24 / 30 / 60, audio kept, re-encoded to AAC or dropped. |
| **Compress photos and audio** | JPEG, WebP, AVIF, HEIC by quality or by size limit; MP3, AAC, Opus, OGG at a chosen bitrate or a size limit, with optional EBU R128 loudness normalisation. |
| **Upscale 2x / 4x** | Lanczos with a light sharpen (fast), FSRCNN (bundled, moderate) or EDSR (photos only, downloaded once, 38 MB per scale, best detail). Video frames stream through FFmpeg pipes; the thumbnail and the ETA show what to expect. |
| **Convert** | Video to MP4, MKV, WebM, MOV, GIF, or its audio track to MP3, M4A, Opus, FLAC, WAV; photos between JPEG, PNG, WebP, AVIF, TIFF, BMP, HEIC; audio between MP3, M4A, Opus, OGG, FLAC, WAV. |
| **Clean outputs** | **Remove metadata** is on per file by default: no tags, chapters or encoder names in the output. Off copies the source metadata as far as the target format allows. |
| **Per-file settings** | Every file opens its own panel; **Apply to all video / photos / audio** copies one file's settings to the others; the defaults for new files live in Settings. |
| **Batch input** | Drag and drop of files and folders (one level deep), several at once; a native multi-select dialog; duplicates are added once. Up to three files run in parallel. |
| **Outputs** | Next to the source or into one folder; a numbered suffix instead of an overwrite; **Show in folder**, **Open**, **Run again**; an optional folder opening and a sound when a batch finishes. |
| **Liquid Glass UI** | A 3:4 portrait window with refracting glass controls, spring motion and an animated background; embedded Inter and Geist Mono fonts; animation stops when nothing moves. |
| **Portable** | One exe, one data folder next to it, no installer. The second launch brings the running window to the front. |
| **Self-updating app** | New releases are detected on launch and then every 30 minutes (or hourly, every 6 hours, daily, weekly), downloaded on **Download** or automatically, verified by size and SHA-256 and installed on **Restart**. |

## Usage

1. Drop files on the window, press **Choose files** or `Ctrl+O`. Each file is probed in the background and shows
   its thumbnail, dimensions, duration, codec and size.
2. Click a file to open its settings: format and codec, encoder, scale and upscale method, quality or size limit,
   frame rate, audio, metadata. The line under the file name shows what will be written.
3. Press **Start** (`Ctrl+Enter`). Each card shows the stage, progress, speed and ETA; a running job can be
   cancelled, a finished or failed one run again.
4. **Show in folder** reveals the output in Explorer; **Clear done** removes finished files from the list.

| Shortcut | Action |
|---|---|
| `Ctrl+O` | Choose files |
| `Ctrl+Enter` | Start every ready file |
| `Delete` | Remove the selected file |
| `Esc` | Close a panel or a popover |

## Settings

The gear in the title bar opens the settings sheet.

| Setting | Meaning |
|---|---|
| **Output** | Next to the source (`<name>_forge.<ext>`) or one folder of your choice. |
| **Suffix** | The part added to the file name, `_forge` by default; may be empty when outputs go to a folder. |
| **Parallel jobs** | 1 to 3 files at a time. |
| **Open the folder when done** | Opens Explorer with the last output selected after a batch. |
| **Sound when done** | A short chime when a batch finishes. |
| **Defaults for new files** | The per-kind settings every new video, photo or audio file starts with. |
| **Check for updates** | Every 30 minutes (default), hour, 6 hours, day or week. |
| **Download updates automatically** | Off by default: a new release is downloaded only on **Download**. |

## Formats and codecs

| Kind | Output formats | Codecs and options |
|---|---|---|
| Video | MP4, MKV, WebM, MOV, GIF; audio extraction to MP3, M4A, Opus, FLAC, WAV | H.264, H.265 / HEVC, AV1 (CPU or NVENC), VP9, ProRes, stream copy; scale original / 2x / 4x / fit 1080p, 720p, 480p; frame rate keep / 24 / 30 / 60; audio keep / AAC 96 to 320 kbps / none; GIF is capped at 15 fps |
| Photo | JPEG, PNG, WebP, AVIF, TIFF, BMP, HEIC | quality 0 to 100 for the lossy formats (PNG is optimised instead); scale original / 2x / 4x / ½ / ¼ |
| Audio | MP3, M4A (AAC), Opus, OGG (Vorbis), FLAC, WAV | 64 to 320 kbps (Opus up to 256; FLAC and WAV are lossless); sample rate keep / 44.1 / 48 kHz; mono / stereo; loudness normalisation to -16 LUFS |

Upscale methods for 2x / 4x: **Lanczos** (resampling with a light sharpen), **FSRCNN** (small deterministic
super-resolution network, bundled) and, for photos, **EDSR** (larger network, downloaded once, slow on a CPU, best
detail). A video patch that asks for EDSR is written with FSRCNN instead.

## Size limit

The limit is a hard cap on the output file, set per file in MB:

- **Video:** the bitrate is computed from the duration and the audio share, encoded in two passes (or NVENC VBR
  with a cap), and the job retries once with a lower target when the result still exceeds the cap.
- **Photo:** the quality is searched down to the limit; when the lowest acceptable quality is still too large, the
  image is downscaled step by step.
- **Audio:** the bitrate comes from the duration, rounded down to the nearest value the format offers.

A file whose cap cannot be met (a lossless format, a limit below what the format can produce) ends with an error
that says so instead of a file that breaks the limit.

## Data and privacy

All runtime data lives in `media-forge_data\` next to `MediaForge.exe` (override with the `MEDIA_FORGE_HOME`
environment variable); nothing is written to the user profile:

| Path | Content |
|---|---|
| `bin\` | `ffmpeg.exe`, `ffprobe.exe` when none is on `PATH`, with their version record |
| `models\` | EDSR model files downloaded on first use, with their source and hash |
| `settings.json` | preferences and the per-kind defaults |
| `cache\` | thumbnails of queued files and the working files of running jobs |
| `logs\` | `app.log` and the full FFmpeg output of a failed job |
| `webview\` | WebView2 profile |
| `updates\` | downloaded app updates and `apply.log` |

Network traffic goes to GitHub only: the release check and download, the FFmpeg build when none is on `PATH`, the
EDSR models on first use. Your files never leave the machine, and no job writes a provenance marker of any kind.

## Updates

The app checks GitHub Releases on every launch in the background and then on a timer: every 30 minutes by default,
or every hour, 6 hours, day or week (**Check for updates** in Settings). A new version is shown on the version
label in the title bar; **Download** fetches it (or the app does it on its own when **Download updates
automatically** is on), verifies the byte size and the SHA-256 against the release's `SHA256SUMS`, and **Restart to
update** installs it: the downloaded exe waits for the app to exit, keeps the old file as `MediaForge.exe.old`
until the new one has started, and relaunches it. If anything fails the current exe stays untouched. Every release
ships two assets: `MediaForge.exe` and `SHA256SUMS`. Release notes live in [`docs/releases/`](docs/releases/).

## Building from source

Requirements: Windows, Python 3.14 (via the `py` launcher), PowerShell 5.1+, FFmpeg on `PATH` for the tests.

```powershell
git clone https://github.com/softoblivion/media-forge.git
cd media-forge
powershell -ExecutionPolicy Bypass -File build\build.ps1
```

The script creates `.venv`, installs the dependencies, runs the unit tests, draws the icon and produces
`release\MediaForge.exe` (one-file PyInstaller build, about 77 MB, most of it OpenCV) with `release\SHA256SUMS`.

| Task | Command |
|---|---|
| Run from source | `set MEDIA_FORGE_HOME=%TEMP%\mf_scratch` then `scripts\run_dev.bat` (`--fake` runs a simulated engine without touching files) |
| Unit tests | `set MEDIA_FORGE_HOME=%TEMP%\mf_tests` and `set PYTHONPATH=src`, then `.venv\Scripts\python.exe -m unittest discover -s tests -t .` |
| Headless job | `.venv\Scripts\python.exe -m media_forge.core.cli <file> [--set key=value ...]` |
| UI in a browser with mock data | `python -m http.server 5173 --directory src\media_forge\ui`, then `http://localhost:5173/?mock=1` |

Architecture, the JS ↔ Python contract, the core pipelines, the UI and build notes:
[ARCHITECTURE](docs/ARCHITECTURE.md) · [BRIDGE_API](docs/BRIDGE_API.md) · [CORE](docs/CORE.md) ·
[UI_DESIGN](docs/UI_DESIGN.md) · [BUILD](docs/BUILD.md)

## Project structure

```
media-forge/
├── src/media_forge/
│   ├── core/          probing, FFmpeg command lines, size targeting, upscale, jobs, settings, paths, CLI
│   ├── models/        bundled FSRCNN model files with their source and hash
│   ├── shell/         entry point, frameless 3:4 window, JS ↔ Python bridge, Windows helpers
│   ├── ui/            HTML / CSS / JS interface: file list, per-file panels, settings sheet, update popover
│   └── vendor/        bundled self-update package
├── tests/             unittest suite (core and shell)
├── build/             PyInstaller spec, build script, icon generator
├── scripts/           development run
└── docs/              architecture, module docs, API contract, build notes, release notes, decisions
```

## Third-party components

Bundled into the executable:

| Component | License |
|---|---|
| [CPython](https://www.python.org/) | PSF-2.0 |
| [pywebview](https://github.com/r0x0r/pywebview) | BSD-3-Clause |
| [pythonnet](https://github.com/pythonnet/pythonnet), clr_loader | MIT |
| bottle, proxy_tools | MIT |
| [NumPy](https://numpy.org/) | BSD-3-Clause |
| [Pillow](https://python-pillow.org/) | MIT-CMU |
| [pillow-heif](https://github.com/bigcat88/pillow_heif) with libheif | BSD-3-Clause, LGPL-3.0 |
| [OpenCV](https://opencv.org/) with the contrib modules | Apache-2.0 |
| [FSRCNN](https://github.com/Saafke/FSRCNN_Tensorflow) model files | Apache-2.0 |
| [PyInstaller](https://pyinstaller.org/) bootloader | GPL-2.0-or-later with the bootloader exception |
| [Inter](https://github.com/rsms/inter), [Geist Mono](https://github.com/vercel/geist-font) fonts | SIL Open Font License 1.1 |

Downloaded at runtime from their official release channels, not redistributed with this project:

| Component | License |
|---|---|
| [FFmpeg](https://ffmpeg.org/) ([yt-dlp/FFmpeg-Builds](https://github.com/yt-dlp/FFmpeg-Builds)), only when none is on `PATH` | GPL |
| [EDSR](https://github.com/Saafke/EDSR_Tensorflow) model files, on first use | Apache-2.0 |

## License

Released under the [GNU General Public License v3.0](LICENSE). Any distributed copy or derivative work must stay
open source under the same license and keep the copyright notices.
