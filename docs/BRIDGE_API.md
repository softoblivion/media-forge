# Bridge API — the JS <-> Python contract

This is the only definition of what the page may call and what the shell sends; the core
(`CORE.md`), the shell (`src/media_forge/shell/bridge.py`) and the UI (`src/media_forge/ui/js/api.js`, the mock
in `js/mock.js`) all code against it. A change here is a change for the core, the shell, the UI and the mock.

## 1. Transport

- The page calls methods on `window.pywebview.api.<name>(...)`; every call returns a Promise. A rejected promise
  carries one English sentence meant for the user (the shell converts every exception).
- Python -> JS: the shell evaluates `window.mediaForge.dispatch(name, payload)`; the page registers
  `window.mediaForge = { dispatch }` before it calls `ready()`. Events emitted before `ready()` are buffered and
  delivered right after it. `item` events are coalesced per item id (only the newest per id is delivered when the
  page is slower than the engine); every other event is delivered in order.
- JSON is produced with `ensure_ascii=False`; U+2028 / U+2029 are escaped. Paths are Windows paths with
  backslashes, never URLs.
- The mock (`index.html?mock=1`, or no pywebview within 1.5 s) implements the same methods and events in JS so the
  UI runs in a plain browser: simulated probes (random sizes, generated thumbnails), progress ticking at a plausible
  speed, one of ten jobs failing, a simulated update status.

## 2. Methods (JS -> Python)

| Method | Arguments | Returns | Notes |
|---|---|---|---|
| `ready()` | | `State` | first call of the page; starts event delivery |
| `add_files(paths)` | `string[]` | `Item[]` | the items added (already listed paths are skipped and reported by a `toast`); probing runs in the background, `item` events follow |
| `pick_files()` | | `Item[]` | native multi-select open dialog (filters: Media, Video, Images, Audio, All); `[]` when cancelled |
| `remove_item(id)` | `string` | `boolean` | a running item is cancelled first; `item_removed` follows |
| `clear(what)` | `"done" \| "errors" \| "idle" \| "all"` | `string[]` | ids removed: finished / failed / not running / everything (running items are cancelled for `all`) |
| `set_item_settings(id, patch)` | `string, object` | `Item` | partial settings of that item's kind (3.3), normalised (3.4); refused for a running item ("The file is being processed.") |
| `apply_to_kind(id)` | `string` | `Item[]` | copies the item's settings to every other idle item of the same kind; returns the changed items |
| `start(ids)` | `string[] \| null` | `string[]` | queues these items in list order and returns the ids queued. Accepted states: `ready`, `error` (retry) and `done` (run again; a new numbered output file, the old one stays). `null` = every `ready` item |
| `start_now(id)` | `string` | `boolean` | runs this item at once on an extra worker, ignoring `parallel_jobs`: a `queued` item leaves the queue and starts; a `ready`, `error` or `done` item starts straight away (same reset as `start`); `false` for a `running` or `probing` item |
| `cancel(id)` | `string` | `boolean` | cancels a queued or running item (back to `ready`) |
| `cancel_all()` | | `number` | items cancelled |
| `get_settings()` | | `Settings` | |
| `set_settings(patch)` | `object` | `Settings` | partial global settings (3.5), validated; `defaults.<kind>` patches are normalised like item settings, `theme` like an enum (unknown -> default) |
| `pick_output_dir()` | | `string \| null` | native folder dialog; the caller then calls `set_settings` |
| `reveal(path)` | `string` | `boolean` | Explorer with the file selected |
| `open_path(path)` | `string` | `boolean` | opens a file or folder with the shell default |
| `open_url(url)` | `string` | `boolean` | opens an `https://` link in the default browser (release notes); anything else returns `false` |
| `capabilities()` | | `Capabilities` | current tool / encoder / model status (also in `State` and the `capabilities` event) |
| `window_minimize()` / `window_close()` | | `null` | title-bar buttons; there is no maximize (fixed window size) |
| `update_status()` | | `UpdateStatus` | self-update status |
| `check_updates()` | | `UpdateStatus` | background check (ignored while one runs); `update` events follow |
| `download_update()` | | `UpdateStatus` | downloads the release found by the last check |
| `apply_update()` | | `boolean` | `true`: the downloaded exe starts in apply mode and the app closes right after the call returns |
| `log(level, message)` | `string, string` | `null` | UI diagnostics into the app log (`debug`, `info`, `warning`, `error`) |

## 3. Payload types

### 3.1 State

```json
{
  "version": "1.0.0",
  "settings": { ...Settings },
  "items": [ ...Item ],
  "capabilities": { ...Capabilities },
  "options": { ...Options },
  "update": { ...UpdateStatus },
  "window": { "focused": true }
}
```

### 3.2 Item

```json
{
  "id": "i1",
  "path": "D:\\clips\\holiday.mp4",
  "name": "holiday.mp4",
  "kind": "video",
  "size": 48213904,
  "status": "ready",
  "probe": {
    "duration": 72.4, "width": 1920, "height": 1080, "fps": 29.97,
    "vcodec": "h264", "acodec": "aac", "bitrate": 5300000,
    "sample_rate": 48000, "channels": 2, "has_audio": true, "format": "mov,mp4"
  },
  "thumb": "data:image/jpeg;base64,...",
  "settings": { ...VideoSettings },
  "progress": 0.0, "stage": null, "eta": null, "speed": null,
  "output": null,
  "error": null,
  "added_at": 1761040000.0
}
```

- `kind`: `video` | `image` | `audio`.
- `status`: `probing` -> `ready` -> `queued` -> `running` -> `done` | `error`; `cancel` returns to `ready`.
  A file that fails to probe is `error` with `probe: null`.
- `probe`: fields that do not apply are `null` (an image has no `duration`, an audio file no `width`); an image's
  `format` is Pillow's format name (`JPEG`, `PNG`, `WEBP`, `AVIF`, `HEIF`, ...); for video / audio it is the first
  two names of ffprobe's format list (`mov,mp4`, `matroska,webm`, `mp3`) and `vcodec` / `acodec` the codec names
  (`h264`, `hevc`, `av1`, `vp9`, `aac`, `mp3`, `opus`, `flac`, ...).
- `thumb`: a data URL, long side 240 px: JPEG for video frames (at 10 % of the duration) and opaque images, PNG
  for images with transparency and for the audio waveform (ffmpeg `showwavespic` on a transparent background);
  `null` until the probe finishes or when it fails.
- `progress` 0..1; `stage` while running: `analyzing` | `pass 1` | `pass 2` | `encoding` | `upscaling` |
  `downloading model` | `writing` | `retrying`; `eta` seconds or `null`; `speed` a short label (`1.8x`, `12 fps`,
  `4.1 MB/s`) or `null`.
- `output` when `done`: `{ "path": "D:\\clips\\holiday_forge.mp4", "size": 9120000, "width": 1920, "height": 1080,
  "duration": 72.4, "ratio": 0.19 }` (`ratio` = output size / source size; `width` / `height` / `duration` `null`
  where they do not apply).
- `error`: one English sentence when `status` is `error`, else `null`.

### 3.3 Item settings (per kind)

VideoSettings (also used when the target is an audio-only format: the video keys are then ignored):

| Key | Values | Default | Meaning |
|---|---|---|---|
| `format` | `mp4` `mkv` `webm` `mov` `gif` `mp3` `m4a` `opus` `flac` `wav` | `mp4` | container; the last five extract the audio track |
| `codec` | `h264` `h265` `av1` `vp9` `prores` `copy` | `h264` | video codec; allowed combinations in `Options.video.formats[].codecs`; `copy` keeps the stream (no scale, no quality, no size limit) |
| `encoder` | `auto` `cpu` `gpu` | `auto` | `gpu` = NVENC for `h264` / `h265` / `av1` when `Capabilities.nvenc` has it; `auto` = gpu when available for the codec, else cpu |
| `quality` | 0..100 integer | 70 | 100 = visually lossless; maps to CRF / CQ per codec (`CORE.md`, 3.2); ignored when `size_limit_mb` is set or `codec` is `copy` |
| `size_limit_mb` | `null` or number > 0 | `null` | hard cap on the output file size |
| `scale` | `none` `2x` `4x` `1080p` `720p` `480p` | `none` | `2x` / `4x` upscale; `NNNp` fits the short side to NNN (never enlarges) |
| `upscale_method` | `lanczos` `fsrcnn` | `lanczos` | used for `2x` / `4x` only. `edsr` is photos only (far below one frame per second on a CPU): a video patch with `edsr` is normalised to `fsrcnn`, and the UI lists the first two methods for video |
| `sharpen` | boolean | `true` | light unsharp mask after an upscale (ignored otherwise) |
| `fps` | `keep` `24` `30` `60` | `keep` | output frame rate; `gif` is capped at 15 |
| `audio` | `keep` `aac` `none` | `keep` | audio in a video output; `keep` copies when the container allows, else `aac` |
| `audio_bitrate_k` | `96` `128` `192` `256` `320` | `192` | for `aac` and for the audio-only formats (`flac` / `wav` ignore it) |
| `strip_metadata` | boolean | `true` | no metadata, chapters or encoder tags in the output |

ImageSettings:

| Key | Values | Default | Meaning |
|---|---|---|---|
| `format` | `jpg` `png` `webp` `avif` `tiff` `bmp` (`heic` when `Capabilities.heif`) | `jpg` | |
| `quality` | 0..100 integer | 85 | lossy formats; `png` / `bmp` / `tiff` ignore it (`png` is optimised instead) |
| `size_limit_mb` | `null` or number > 0 (fractions allowed, e.g. 0.5) | `null` | hard cap: quality search, then a downscale ladder (`CORE.md`, 6) |
| `scale` | `none` `2x` `4x` `0.5x` `0.25x` | `none` | |
| `upscale_method` | `lanczos` `fsrcnn` `edsr` | `lanczos` | for `2x` / `4x` only |
| `sharpen` | boolean | `true` | after an upscale only |
| `strip_metadata` | boolean | `true` | drops EXIF, XMP, IPTC, text chunks; the ICC profile stays |

AudioSettings:

| Key | Values | Default | Meaning |
|---|---|---|---|
| `format` | `mp3` `m4a` `opus` `ogg` `flac` `wav` | `mp3` | |
| `bitrate_k` | `64` `96` `128` `160` `192` `256` `320` | `192` | `flac` / `wav` ignore it; `opus` is clamped to 256 |
| `size_limit_mb` | `null` or number > 0 | `null` | bitrate from the duration, then the nearest lower value of the list; `flac` / `wav` cannot be capped (error) |
| `sample_rate` | `keep` `44100` `48000` | `keep` | |
| `channels` | `keep` `1` `2` | `keep` | |
| `normalize` | boolean | `false` | EBU R128 loudness normalisation (`loudnorm`, -16 LUFS) |
| `strip_metadata` | boolean | `true` | |

### 3.4 Normalisation

`set_item_settings`, `apply_to_kind` and the `defaults` part of `set_settings` return normalised settings: unknown
keys dropped, every value replaced by the nearest allowed one (numbers clamped, unknown enum values -> default),
and incompatible combinations resolved in this order: a `codec` not allowed for the `format` -> the format's first
codec; `codec: copy` with `scale != none`, a `size_limit_mb` or `fps != keep` -> `h264`; `encoder: gpu` for a codec
without NVENC support -> `auto`; `upscale_method` other than `lanczos` with `scale` that is not `2x` / `4x` is kept
(it just does not apply). The UI shows the returned settings, never its own guess.

### 3.5 Settings (global)

```json
{
  "output_mode": "beside",
  "output_dir": "",
  "suffix": "_forge",
  "parallel_jobs": 1,
  "open_when_done": false,
  "sound_when_done": true,
  "auto_download_updates": true,
  "theme": "nova",
  "defaults": { "video": { ...VideoSettings }, "image": { ...ImageSettings }, "audio": { ...AudioSettings } }
}
```

- `output_mode`: `beside` (next to the source) | `folder` (`output_dir`, must exist and be writable, else the patch
  is refused with "The output folder does not exist.").
- `suffix`: 0..24 characters valid in a Windows file name; an empty suffix is allowed only in mode `folder`
  (the file may otherwise overwrite its source; the shell refuses the patch with "A suffix is needed when files are
  written next to the source.").
- `parallel_jobs`: 1..3.
- `open_when_done`: acted on by the shell: on `batch_done` with at least one finished file it opens the folder of
  the last output in Explorer with that file selected (one window per batch).
- `sound_when_done`: acted on by the page (Web Audio chime on `batch_done`); the shell plays nothing.
- `auto_download_updates`: default `true`; a release found by a check is downloaded in the background, installing
  still needs a restart. The check period is fixed at 5 minutes and is not a setting; a file written by an earlier
  version may still carry `update_check_interval`, which is ignored and dropped on the next save.
- `theme`: one of the Liquid UI kit's four theme names, `dusk` | `nova` | `frost` | `bloom`; default `nova`. An
  unknown value in a patch or in the file is replaced by the default (never refused). The page applies the
  confirmed value (`applyTheme` of the kit) and mirrors it in `localStorage` so the next launch paints the right
  theme before `ready()` returns; the shell only stores it.

### 3.6 Capabilities

```json
{
  "ffmpeg": "ready",
  "ffmpeg_version": "9.0.2",
  "ffmpeg_path": "C:\\...\\ffmpeg.exe",
  "nvenc": { "h264": true, "h265": true, "av1": true },
  "gpu_name": "NVIDIA GeForce RTX 4070 Ti SUPER",
  "avif": true,
  "heif": true,
  "models": { "fsrcnn": "ready", "edsr": "missing" }
}
```

- `ffmpeg`: `probing` | `ready` | `downloading` | `missing` (no copy and the download failed; message in a `toast`).
- `nvenc`: result of the one-frame test encode per codec; all `false` without an NVIDIA GPU. `gpu_name` may be `null`.
- `models.<name>`: `ready` | `missing` | `downloading` | `error`.

### 3.7 Options

The option lists the UI renders (labels included), produced by `core/catalog.py` so the UI and the mock never keep
their own copy of what the engine accepts:

```json
{
  "video": {
    "formats": [
      { "id": "mp4",  "label": "MP4",  "codecs": ["h264", "h265", "av1"], "audio": true },
      { "id": "mkv",  "label": "MKV",  "codecs": ["h264", "h265", "av1", "vp9", "copy"], "audio": true },
      { "id": "webm", "label": "WebM", "codecs": ["vp9", "av1"], "audio": true },
      { "id": "mov",  "label": "MOV",  "codecs": ["h264", "h265", "prores"], "audio": true },
      { "id": "gif",  "label": "GIF",  "codecs": [], "audio": false },
      { "id": "mp3",  "label": "MP3 · audio only", "codecs": [], "audio": true },
      { "id": "m4a",  "label": "M4A · audio only", "codecs": [], "audio": true },
      { "id": "opus", "label": "Opus · audio only", "codecs": [], "audio": true },
      { "id": "flac", "label": "FLAC · audio only", "codecs": [], "audio": true },
      { "id": "wav",  "label": "WAV · audio only", "codecs": [], "audio": true }
    ],
    "codecs": [
      { "id": "h264", "label": "H.264", "gpu": true },
      { "id": "h265", "label": "H.265 / HEVC", "gpu": true },
      { "id": "av1",  "label": "AV1", "gpu": true },
      { "id": "vp9",  "label": "VP9", "gpu": false },
      { "id": "prores", "label": "ProRes", "gpu": false },
      { "id": "copy", "label": "Copy stream", "gpu": false }
    ],
    "encoders": [ { "id": "auto", "label": "Auto" }, { "id": "cpu", "label": "CPU" }, { "id": "gpu", "label": "GPU" } ],
    "scales": [
      { "id": "none", "label": "Original" }, { "id": "2x", "label": "2×" }, { "id": "4x", "label": "4×" },
      { "id": "1080p", "label": "Fit 1080p" }, { "id": "720p", "label": "Fit 720p" }, { "id": "480p", "label": "Fit 480p" }
    ],
    "fps": [ { "id": "keep", "label": "Keep" }, { "id": "24", "label": "24" }, { "id": "30", "label": "30" }, { "id": "60", "label": "60" } ],
    "audio": [ { "id": "keep", "label": "Keep" }, { "id": "aac", "label": "AAC" }, { "id": "none", "label": "None" } ],
    "audio_bitrates": [96, 128, 192, 256, 320]
  },
  "image": {
    "formats": [
      { "id": "jpg", "label": "JPEG", "lossy": true }, { "id": "png", "label": "PNG", "lossy": false },
      { "id": "webp", "label": "WebP", "lossy": true }, { "id": "avif", "label": "AVIF", "lossy": true },
      { "id": "tiff", "label": "TIFF", "lossy": false }, { "id": "bmp", "label": "BMP", "lossy": false },
      { "id": "heic", "label": "HEIC", "lossy": true }
    ],
    "scales": [
      { "id": "none", "label": "Original" }, { "id": "2x", "label": "2×" }, { "id": "4x", "label": "4×" },
      { "id": "0.5x", "label": "½" }, { "id": "0.25x", "label": "¼" }
    ]
  },
  "audio": {
    "formats": [
      { "id": "mp3", "label": "MP3", "lossy": true }, { "id": "m4a", "label": "M4A · AAC", "lossy": true },
      { "id": "opus", "label": "Opus", "lossy": true }, { "id": "ogg", "label": "OGG · Vorbis", "lossy": true },
      { "id": "flac", "label": "FLAC", "lossy": false }, { "id": "wav", "label": "WAV", "lossy": false }
    ],
    "bitrates": [64, 96, 128, 160, 192, 256, 320],
    "sample_rates": [ { "id": "keep", "label": "Keep" }, { "id": "44100", "label": "44.1 kHz" }, { "id": "48000", "label": "48 kHz" } ],
    "channels": [ { "id": "keep", "label": "Keep" }, { "id": "1", "label": "Mono" }, { "id": "2", "label": "Stereo" } ]
  },
  "upscale_methods": [
    { "id": "lanczos", "label": "Lanczos", "hint": "Resampling with a light sharpen. Fast." },
    { "id": "fsrcnn", "label": "FSRCNN", "hint": "Small deterministic super-resolution network, bundled. Moderate." },
    { "id": "edsr", "label": "EDSR", "hint": "Photos only. Larger deterministic network, downloaded once (38 MB per scale). Slow, best detail." }
  ]
}
```

`heic` is listed only when `Capabilities.heif` is true. The ids above are the complete sets accepted by 3.3.

### 3.8 UpdateStatus

The vendored `selfupdate` package's payload, unchanged: `stage` (`idle` `checking` `up_to_date` `available` `downloading` `ready` `error`), `current`,
`latest`, `notes`, `size`, `published_at`, `progress`, `message`, `checked_at`, `can_apply`.

## 4. Events (Python -> JS)

| Event | Payload | When |
|---|---|---|
| `item` | `Item` | any change of an item: probe finished, settings normalised by the engine, status, progress (coalesced per id) |
| `item_removed` | `{ "id": "i1" }` | after `remove_item` / `clear` |
| `items_added` | `{ "items": [ ...Item ] }` | files dropped on the window (the shell adds them itself; `pick_files` / `add_files` return their items instead) |
| `settings` | `Settings` | after a change (also from another window state, e.g. a refused patch restores the old value) |
| `capabilities` | `Capabilities` | tool discovery, download progress of ffmpeg or a model, NVENC probe finished |
| `toast` | `{ "kind": "info" \| "success" \| "error", "message": "..." }` | one sentence for the user |
| `update` | `UpdateStatus` | every change of the self-update status |
| `window` | `{ "focused": true }` | focus changes (the background drift pauses when unfocused) |
| `batch_done` | `{ "done": 3, "failed": 0, "cancelled": 0 }` | the queue ran empty after at least one job |

## 5. Drag and drop

The page handles `dragenter` / `dragover` / `drop` only to show its drop veil and to prevent navigation; it never
reads paths from the DOM event (WebView2 exposes none there). The shell registers a Python DOM listener
(`window.dom.document.events.drop`) once the page has loaded, reads `pywebviewFullPath` of every dropped file,
expands dropped folders one level (media files inside, no recursion), calls the engine's `add_files` and emits
`items_added`. In the mock, dropped `File` objects become simulated items by name.

## 6. Shell rules

- The js_api object exposes exactly the methods of section 2 as public names; everything else is underscored.
- Every method is wrapped: exceptions become a rejected promise with a user-facing sentence; unexpected ones are
  logged with the traceback.
- Window close: cancel every running job, stop the engine and the updater scheduler, save settings, then exit with
  `os._exit(0)` after `webview.start` returns (WebView2 helper threads).
