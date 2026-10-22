# Media Forge — architecture

## 1. Goal

A Windows desktop utility, shipped as one portable `MediaForge.exe`, that takes video, photo and audio files
(dropped or picked, many at once) and, per file, with its own settings:

1. Compresses them (quality slider, or an optional size limit in MB that the output must not exceed).
2. Upscales video and photos 2x / 4x without a generative model: Lanczos resampling with a light sharpen by
   default, or a small deterministic super-resolution network (FSRCNN, EDSR; OpenCV `dnn_superres`) as an option.
   Nothing in the app generates pixels from noise or text; the models are learned interpolation filters.
3. Converts between formats (video containers and codecs, audio extraction, image formats, audio formats).
4. Writes outputs without any metadata by default (no EXIF / XMP / IPTC / C2PA manifests, no encoder tags), so a
   file never carries a provenance marker of any kind; the only thing kept is the ICC colour profile.
5. Keeps every file it writes next to its own exe (`media-forge_data\`); outputs go next to the source file by
   default (`<name>_forge.<ext>`) or into one folder the user picks.
6. Has a Liquid Glass UI in a 3:4 portrait window with an animated, layered background.

## 2. Stack and why

| Layer | Choice | Why |
|---|---|---|
| Window / UI host | pywebview 6.2.1, `edgechromium` backend (WebView2) | The workspace default (`_workspace\RECIPES.md`, section 4): Chromium is the only engine where `backdrop-filter: url(#svg-filter)` gives real refraction; WebView2 ships with Windows 11. |
| UI | Plain HTML / CSS / JS (ES modules), no build step, no network, fonts embedded | Same as Media Saver and Frame Notes; the glass material, controls, background, fonts and the version / update popover come from the shared design kit `liquid_ui`, vendored as `ui/vendor/liquid_ui` (master in the workspace's `_shared`, docs `LIQUID_UI.md` there; never edited inside this repository). |
| Backend | Python 3.14 | pywebview, pythonnet, PyInstaller, NumPy, Pillow and OpenCV all ship 3.14 wheels. |
| Video / audio | `ffmpeg.exe` + `ffprobe.exe` as subprocesses: `media-forge_data\bin` first, then PATH, else downloaded once from yt-dlp/FFmpeg-Builds (`RECIPES.md`, section 3) | Every codec and container the app offers is one ffmpeg invocation; NVENC (H.264 / HEVC / AV1) is used when the machine has it. |
| Images | Pillow (JPEG, PNG, WebP, AVIF, TIFF, BMP; HEIC through `pillow-heif` when importable) | AVIF is built into Pillow 11+ wheels; everything is in-process, no temp files. |
| Upscale | OpenCV (`opencv-contrib-python-headless`, `dnn_superres`) + NumPy; FSRCNN models bundled, EDSR downloaded on demand | Deterministic CNN super-resolution, CPU only, no GAN, no diffusion, no prompt. Video frames stream through ffmpeg pipes. |
| Self-update | GitHub Releases of `softoblivion/media-forge` (assets `MediaForge.exe` + `SHA256SUMS`) through the vendored `selfupdate` package: check on launch and then every 5 minutes (fixed period, no setting), verified download, rename-in-place swap on restart (`_workspace\RECIPES.md`, section 1) | The workspace's shared updater; no installer, no helper process. |
| Packaging | PyInstaller one-file, windowed, own icon | One exe to double-click. |

## 3. Repository layout

```
media-forge/
  README.md, pyproject.toml, requirements.txt, requirements-dev.txt
  docs/ARCHITECTURE.md       this file
  docs/BRIDGE_API.md         JS <-> Python contract: methods, events, payload types, option tables (the only definition)
  docs/CORE.md               core modules: probing, command lines, quality mapping, size targeting, upscale, jobs
  docs/UI_DESIGN.md          design system, Liquid Glass material, background, motion, components
  docs/BUILD.md              dev run, tests, build, troubleshooting
  docs/releases/vX.Y.Z.md    release notes
  docs/decisions/            project decision records
  src/media_forge/__init__.py        __version__ (the only place the version lives)
  src/media_forge/core/paths.py      AppPaths: data folder, atomic writes (the only module that builds data paths)
  src/media_forge/core/settings.py   global settings + per-kind default settings, persistence, validation
  src/media_forge/core/catalog.py    option tables, defaults, settings validation and normalisation, output names
  src/media_forge/core/tools.py      ffmpeg / ffprobe discovery, download, capability probe (encoders, NVENC)
  src/media_forge/core/procs.py      subprocess runner: no console window, UTF-8, Job Object kill tree, progress pipe
  src/media_forge/core/probe.py      kind detection, ffprobe / Pillow metadata, thumbnails (video frame, image, waveform)
  src/media_forge/core/video.py      video jobs: filter chain, codec arguments, two-pass, size targeting, GIF, audio extract
  src/media_forge/core/image.py      image jobs: scale, sharpen, quality search to a size limit, metadata stripping
  src/media_forge/core/audio.py      audio jobs: format, bitrate, sample rate, channels, loudness, size limit
  src/media_forge/core/upscale.py    Lanczos + sharpen, dnn_superres models (bundled FSRCNN, downloaded EDSR), frame pipeline
  src/media_forge/core/jobs.py       queue, worker threads, item state machine, cancel, progress events
  src/media_forge/core/engine.py     facade used by the bridge (section 6)
  src/media_forge/core/cli.py        headless harness: python -m media_forge.core.cli <file> [--set key=value ...]
  src/media_forge/core/selfupdate.py Media Forge's configuration of the vendored self-updater (repo, asset, names)
  src/media_forge/vendor/selfupdate/ vendored copy of D:\Projects\_shared\selfupdate (never edited here; synced by
                                     _tools\vendor-sync.ps1; VENDORED.txt names the version)
  src/media_forge/models/            bundled model files (FSRCNN_x2.pb, FSRCNN_x4.pb) + MODELS.md (source, licence, hash)
  src/media_forge/shell/main.py      entry: single instance, window (3:4, frameless), drop listener, lifecycle
  src/media_forge/shell/bridge.py    pywebview js_api class, event pump
  src/media_forge/shell/winutil.py   DWM corners and border colour, the fixed-size window hook, Explorer, message box, mutex
  src/media_forge/ui/                index.html, css/ (app tokens and app-only rules), js/ (incl. js/mock.js), assets/ (icon)
  src/media_forge/ui/vendor/liquid_ui/ vendored design kit: css/, js/, fonts/ (synced by _tools\vendor-sync.ps1; VENDORED.txt)
  tests/                     unittest suite (test_core_*, test_shell_*)
  build/                     entry.py, MediaForge.spec, build.ps1, make_icon.py, icon.ico
  scripts/run_dev.bat        run from source
```

Build output (`release/`, `build/work`, `build/dist`), `.venv/` and the runtime folder created when running from
source (`media-forge_data/`) are not tracked.

Rules for all code: comments, docs and user-facing strings in English; tests use `unittest`
(`python -m unittest discover -s tests -t .` with `PYTHONPATH=src`). Each module keeps its owning document current.

Module table (owner = the session or worker that may write there during a task; `_workspace\PARALLEL.md`):

| Module | Path | Owning doc | Owner |
|---|---|---|---|
| Core | `src/media_forge/core/**` except `selfupdate.py`, `src/media_forge/models/**`, `tests/test_core_*` | `CORE.md` | core worker |
| Self-update adapter | `src/media_forge/core/selfupdate.py`, `src/media_forge/vendor/**` (synced only), `tests/test_shell_selfupdate.py` | `ARCHITECTURE.md` 5.7 | shell worker |
| UI | `src/media_forge/ui/**` | `UI_DESIGN.md` | ui worker |
| Shell and build | `src/media_forge/shell/**`, `src/media_forge/__init__.py`, `tests/test_shell_*`, `build/`, `scripts/`, `pyproject.toml`, `requirements*.txt`, `BUILD.md` | `BUILD.md` | shell worker |
| Contracts and docs | `README.md`, `ARCHITECTURE.md`, `BRIDGE_API.md`, release notes | `ARCHITECTURE.md`, `BRIDGE_API.md` | integrator |

## 4. Runtime data (portable)

`AppPaths` (`core/paths.py`) resolves everything from one root:

- app dir = the folder of `MediaForge.exe` (frozen) or the project root (from source); never the PyInstaller
  extraction folder.
- root = `%MEDIA_FORGE_HOME%` if set (tests set it to a scratch folder), else `<app dir>\media-forge_data`.
- `bin/` — `ffmpeg.exe`, `ffprobe.exe` when not taken from PATH, `versions.json`.
- `models/` — downloaded super-resolution models (`EDSR_x2.pb`, `EDSR_x4.pb`) and `models.json` (source, hash).
- `settings.json` — global settings and the per-kind default settings.
- `cache/thumbs/` — thumbnails of queued files (removed with the item, folder cleared on start).
- `cache/jobs/<id>/` — two-pass logs, model frame pipes' scratch; removed when the job ends.
- `logs/app.log` (rotating 1 MB x 3), `logs/jobs/<id>.log` (full ffmpeg stderr of a failed job).
- `webview/` — WebView2 profile (`storage_path`).
- `updates/` — downloaded `MediaForge-<version>.exe`, `apply.log`, `applied.json` (the vendored updater's folder).

Outputs: next to the source (`<stem>_forge.<ext>`, then `<stem>_forge (2).<ext>` ...) in `output_mode = "beside"`,
or in `settings.output_dir` in mode `"folder"`. No path depends on `%LOCALAPPDATA%`, `%USERPROFILE%` or a drive
letter (`_workspace\CONVENTIONS.md`, "Portable projects").

## 5. Behaviour

### 5.1 Adding files

Dropped (the shell reads `pywebviewFullPath` through a Python-side DOM listener, `_workspace\NOTES.md`) or picked
in the native dialog. Each path becomes an `Item` (`BRIDGE_API.md`, 3.2) with the default settings of its kind,
status `probing`; a background thread runs ffprobe / Pillow, builds the thumbnail and emits the `item` event with
status `ready`. A path that is not a media file becomes status `error` with a message. Duplicate paths are added
once (toast "Already in the list").

Kind detection: extension first (`CORE.md`, 2), confirmed by the probe (a `.gif` with one frame is an image, an
`.mp4` without a video stream is audio).

### 5.2 Settings

Each item carries its own settings (kind-specific, `BRIDGE_API.md`, 3.3). The per-kind defaults are editable in
Settings and apply to newly added files. "Apply to all <kind>" copies one item's settings to every other item of
that kind that is not running. Every patch goes through `catalog.normalize(kind, settings)`: unknown keys are
dropped, values clamped to the option tables, incompatible combinations resolved (a `copy` codec with a scale or a
size limit becomes `h264`; a GIF has no audio; an audio-only format ignores video options).

### 5.3 Running

`start(ids)` queues the ready items; `settings.parallel_jobs` workers (1..3) take them in order. One item = one
job (`CORE.md`, 5): video and audio jobs are ffmpeg processes with `-progress pipe:1`, model upscale of a video is
a decode pipe -> NumPy -> `dnn_superres` -> encode pipe loop, image jobs are in-process Pillow / OpenCV work in the
worker thread. Progress, stage, ETA and speed flow through `item` events (coalesced per item). Cancel kills the
process tree (Win32 Job Object) and deletes the partial output. The output path is decided when the job starts;
an existing file gets a numbered suffix, never overwritten.

### 5.4 Size limit

`size_limit_mb` (optional, per item) is a hard cap: video = bitrate targeting with two passes (or NVENC VBR with a
cap) and one corrective retry when the result still exceeds the cap; image = quality search, then a downscale
ladder; audio = bitrate from the duration, clamped to the format's range. An item whose cap cannot be met (for
example a lossless format) ends with `error` and a message that says so (`CORE.md`, 6).

### 5.5 Metadata

`strip_metadata` (default on) writes ffmpeg outputs with `-map_metadata -1 -map_chapters -1` and bit-exact flags
(no `encoder` / `Lavf` tags), and Pillow outputs without EXIF, XMP, IPTC and text chunks; the ICC profile is kept.
Nothing in the app ever writes a C2PA manifest, an IPTC `DigitalSourceType` or any other provenance marker.
Off = the source metadata is copied as far as the target format allows.

### 5.6 Tools and models

On start the engine looks for ffmpeg / ffprobe (`bin/` first, PATH second); when both are missing it downloads
the static build in the background (`RECIPES.md`, 3) and shows status `downloading`; jobs wait. Capabilities
(`BRIDGE_API.md`, 3.6) are probed once per launch: encoder list, NVENC availability by a 1-frame test encode,
AVIF / HEIF support, model files present. FSRCNN x2 / x4 ship inside the exe; EDSR x2 / x4 (about 38 MB each)
are downloaded on first use into `models/` and verified by SHA-256 (`CORE.md`, 4).

### 5.7 Self-update

`core/selfupdate.py` configures the vendored updater (`REPO = "softoblivion/media-forge"`, `ASSET_NAME =
"MediaForge.exe"`, `APP_NAME = "Media Forge"`, `RELEASE_URL_ENV = "MEDIA_FORGE_RELEASE_URL"`, logger
`media_forge.selfupdate`) exactly like Frame Notes' `core/selfupdate.py`. `shell/main.py` handles `APPLY_FLAG`
before anything else, calls `startup()` and `check_background()` after the window is shown, starts the
`UpdateScheduler` (fixed period of 5 minutes with a random offset; the wait doubles after failed checks up to one
hour; no check without a network connection), downloads automatically when `auto_download_updates` is on (the
default), and closes the window after a successful `apply()`. The UI shows the status in the title bar (version
label with a dot when a release is available), a popover with notes, Download / Restart buttons, and the setting
for the automatic download.

## 6. Engine facade

`core/engine.py` — `Engine(emit, version)`; the bridge calls only these:

| Method | Does |
|---|---|
| `start()` / `stop()` | tool discovery and capability probe in the background; stop cancels every running job and joins the workers |
| `state()` | the full `State` (`BRIDGE_API.md`, 3.1) |
| `add_files(paths)` | adds items (deduplicated), starts probing; returns the new items |
| `remove_item(id)`, `clear(kind_of_clear)` | removes an item (cancels it when running) / removes `done`, `error` or all idle items |
| `set_item_settings(id, patch)` | normalises and stores; returns the item |
| `apply_to_kind(id)` | copies the item's settings to the other idle items of the same kind; returns the changed items |
| `start_items(ids)` / `start_now(id)` / `cancel(id)` / `cancel_all()` | queue control; `start_now` runs one item at once on an extra worker, ignoring `parallel_jobs` |
| `get_settings()` / `set_settings(patch)` | global settings and per-kind defaults |
| `capabilities()` | tools, encoders, models |
| `options()` | the option tables of `BRIDGE_API.md`, 3.7 |

Events emitted by the engine: `item`, `item_removed`, `settings`, `capabilities`, `toast` (`BRIDGE_API.md`, 4).

## 7. Window

Frameless, 3:4 portrait, fixed size (global rule "Fixed window size by default", 2026-10-21; 1.0 was resizable
with an aspect lock): 630 x 840 logical pixels, scaled down by whole steps that keep 3:4 when the monitor work
area is smaller, not resizable (no sizing border, no size hook, no maximize), minimize and close from the page's
title bar, DWM rounded corners, DWM border colour set to the page background (`DWMWA_BORDER_COLOR`) so no light
frame line shows at the top. The page covers the whole client area (no resize strip). Single instance by a named
mutex; a second launch brings the window to the front.
