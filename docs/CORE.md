# Core — probing, jobs, encoding, upscale

Scope: `src/media_forge/core/**` except `selfupdate.py`, `src/media_forge/models/**`,
`tests/test_core_*`, `tests/core_support.py`. Contract with the shell and the UI: `BRIDGE_API.md` (payload types,
option tables, settings keys and defaults). This document records how the core does its work and is kept equal to
the code; section 9 lists every point where the code differs from, or adds to, `BRIDGE_API.md`.

## 1. Modules

| Module | Responsibility |
|---|---|
| `paths.py` | `AppPaths` (`MEDIA_FORGE_HOME` override, `media-forge_data` beside the exe / project root): `bin`, `models`, `settings.json`, `cache/thumbs`, `cache/jobs/<job id>`, `logs`, `logs/jobs/<job id>.log`, `webview`, `updates`, `bin/versions.json`, `models/models.json`; `bundled_models_dir()` (`media_forge/models` inside the package, also inside the PyInstaller extraction folder); `atomic_write_bytes/text/json`, `replace_file` (retry on a locked target), `remove_path` |
| `errors.py` | `UserError(message)` (one sentence for the user), `Cancelled`, and the shared message constants |
| `settings.py` | `Settings(paths, persist=True)`: load / save `settings.json` (`version: 1`), `validate(current, patch, caps)` for global keys and `defaults.<kind>` (through `catalog.merge`), `sanitize` of a stored file key by key; a broken file is kept as `settings.json.bad` and replaced by the defaults |
| `catalog.py` | `options(caps)` (exactly `BRIDGE_API.md` 3.7, `heic` only with HEIF), `DEFAULTS`, `normalize(kind, settings, caps)` and `merge(kind, current, patch, caps)` (3.4), `output_path(source, kind, settings, global, reserve)`, extension tables, `kind_for_extension`, `dialog_filters()` for the shell's open dialog |
| `tools.py` | `find_ffmpeg(paths)`; `download_ffmpeg(paths, on_progress, cancel)`; `http_download` (streamed, SHA-256 while writing, cancel); `probe_capabilities(tool)`; `public_capabilities(caps)` |
| `procs.py` | `run(args, on_line, capture, cancel, timeout, cwd)`, `spawn`, `JobObject` (Win32 kill-on-close tree), `LinePump` / `BytesPump`, `ProgressParser` for `-progress pipe:1`, `format_cmd` |
| `probe.py` | `detect_kind`, `probe(path, ffprobe) -> ProbeInfo` (`ProbeInfo.public()` = `Item.probe`), `thumbnail(path, kind, info, ffmpeg) -> data URL` |
| `ffjob.py` | `Plan` / `Step` (what a video or audio job runs; `Plan.describe()` prints the command lines), `run_step(s)` with progress mapping, `metadata_args(strip, video, audio)`, `FFmpegError` (+ NVENC start failure markers), `error_message(stderr tail)` |
| `video.py` | `plan_video(...) -> Plan` and `run_video(...)`: filter chain, codec arguments, two-pass, size targeting with one corrective retry, GPU -> CPU fallback, GIF, audio extraction (delegated to `audio.py`), the model-upscale pipeline |
| `image.py` | `run_image(src, info, settings, out, ctx, get_model)`: Pillow pipeline of section 5.2 |
| `audio.py` | `plan_audio` / `run_audio` (section 5.3), `size_limited_kbps`, `audio_settings_from_video` |
| `upscale.py` | `lanczos`, `sharpen`, `upscale_array` (RGB / RGBA / grey), `Model` (dnn_superres, tiles), `MODEL_FILES` (pinned SHA-256), `ensure_model` (EDSR download), `verify_model`, `model_status`, `frame_pipeline` (decoder -> model -> encoder) |
| `jobs.py` | `Item`, `JobContext(cancel, report)`, `JobQueue(emit, runner, workers)`: item table, state machine, worker threads, cancel, throttled `item` events, `batch_done` |
| `engine.py` | `Engine(emit, version, *, paths=None, persist_settings=True, allow_download=True, use_path_ffmpeg=True)`: the facade of `ARCHITECTURE.md` 6 |
| `cli.py` | `python -m media_forge.core.cli <file>... [--set key=value]... [--out DIR] [--suffix TEXT] [--plan-only] [--json] [--timeout S] [-v]`: probes, prints the plan (ffmpeg command lines), runs the jobs with a progress line, prints output path, size, ratio and dimensions; settings stay in memory (`persist_settings=False`); exit code 0 = all done, 1 = a job or probe failed, 2 = a refused setting |

Logging: loggers `media_forge.core.<module>`; the shell installs the handlers. The core never configures logging
(the CLI does with `-v`).

## 2. Kinds, probing, thumbnails

Extension table (lower-case): video `.mp4 .m4v .mov .mkv .webm .avi .wmv .flv .ts .m2ts .mts .mpg .mpeg .3gp`
(+ animated `.gif`); image `.jpg .jpeg .png .webp .avif .heic .heif .tif .tiff .bmp .gif .jxl` (`.jxl` only when
Pillow can read it); audio `.mp3 .m4a .aac .wav .flac .ogg .opus .wma .aiff .aif`. An unknown extension is added
with the provisional kind `video` and probed with ffprobe, then Pillow.

Probe: images through Pillow (`pillow_heif.register_heif_opener()` when importable; `Image.MAX_IMAGE_PIXELS =
400 000 000`); a GIF with more than one frame is a video (probed by ffprobe, `format = "gif"`). Everything else
through `ffprobe -v error -print_format json -show_format -show_streams` (30 s timeout). Video streams with
`disposition.attached_pic` (cover art) are ignored, so an mp3 with a cover is audio and an mp4 without a video
stream is audio. A container ffprobe reads only as an image pipe (`image2`, `png_pipe`, ...) is tried with Pillow.
No stream at all, or both readers fail -> `UserError("Not a media file.")`; a missing path -> "The file was not
found."; a video / audio file while ffmpeg is unavailable -> "ffmpeg is not available.". Pillow images are not
`verify()`-ed (PNG refuses `verify()` after any chunk access); a truncated file fails at job time instead.

`ProbeInfo` public fields = `Item.probe`; `format` is Pillow's format name for images and the first two names of
ffprobe's `format_name` for video / audio (`mov,mp4`, `matroska,webm`, `mp3`). `fps` from `r_frame_rate`
(`avg_frame_rate` when `r_frame_rate` is missing or above 240), rounded to 3 decimals; the exact fraction is kept
internally for the frame pipeline. `duration` from the format section, stream duration as fallback; `bitrate` from
`format.bit_rate` or size / duration. Rotation (`side_data_list` display matrix, else the `rotate` tag) of 90 / 270
swaps width and height; EXIF orientation 5..8 does the same for images. Internal fields: `pix_fmt` (10-bit test),
`nb_frames`, `rotation`, `audio_bitrate`, `color_space`, `image_mode`, `has_alpha`, `n_frames`.

Thumbnails (long side 240 px; failures give `null`, never an item error):
- video: `ffmpeg -ss <10 % of duration> -i <src> -map 0:v:0 -frames:v 1 -vf
  scale=w=240:h=240:force_original_aspect_ratio=decrease -f image2pipe -c:v png -` (retried at 0 s), then Pillow
  -> JPEG quality 80 data URL;
- image: Pillow `draft` (JPEG) + `exif_transpose` + `thumbnail(240, 240)`; JPEG quality 80, or PNG when the image
  has transparency;
- audio: `-filter_complex [0:a:0]showwavespic=s=480x240:colors=#9fb8ff` to PNG, scaled to 240 x 120, kept as a
  transparent PNG data URL.
Thumbnails live in memory only (`Item.thumb`); `cache/thumbs` is created and emptied on start but not used.

## 3. Video jobs

### 3.1 Command line

```
ffmpeg -hide_banner -nostdin -nostats -v error -y -progress pipe:1 [-hwaccel auto] -i <src>
       -map 0:v:0 [-map 0:a:0?] [-vf <filters>] <codec args> <audio args> <metadata args>
       [-movflags +faststart] [-loop 0] -f <muxer> <out>
```

`-hwaccel auto` only when the encoder is NVENC and no model pipeline runs. `-v error` keeps stderr to error lines,
which become the user's message (section 6). Muxers: mp4 `mp4`, mkv `matroska`, webm `webm`, mov `mov`, gif `gif`.

Filter chain in order: `[scale]` -> `[unsharp]` -> `[crop to even]` -> `format=<pix>` -> `[fps=N]` (GIF: `fps=<min(15,
fps)>` and the palette chain instead of `format`). Scale: `2x` / `4x` = `scale=iw*F:ih*F:flags=lanczos`; `NNNp` =
explicit even dimensions computed from the displayed size (`scale=W:H:flags=lanczos`, short side = NNN, the long
side rounded to an even number), and no scale when the short side is already `<= NNN` (never enlarges). Odd source
dimensions are cropped by one pixel (`crop=W:H:0:0`) because 4:2:0 needs even sizes. Sharpen after a 2x / 4x
upscale: `unsharp=5:5:<a>:5:5:0.0` (luma only), `a` = 0.6 / 0.45 for Lanczos 2x / 4x and 0.3 / 0.22 after a model
(the network output is already crisp). Autorotation stays on, so rotated sources come out upright and the rotate
tag is consumed.

Pixel format (set by the `format` filter only, no separate `-pix_fmt`): `yuv420p`; `yuv420p10le` (CPU) / `p010le`
(NVENC) for h265 / av1 when the source is 10-bit and no model pipeline runs; prores `yuv422p10le`.

Metadata, `strip_metadata` on: `-map_metadata -1 -map_chapters -1 -fflags +bitexact -flags:v +bitexact
-metadata:s:v:0 encoder= [-flags:a +bitexact -metadata:s:a:0 encoder=]` (the empty `encoder=` removes the stream
tag that ffmpeg otherwise writes as `Lavc <encoder>` even in bit-exact mode); libx264 output also gets `-bsf:v
filter_units=remove_types=6` (drops the x264 version / settings SEI) and libx265 runs with `info=0` in
`-x265-params` plus `-bsf:v filter_units=remove_types=39` (x265 puts its info SEI into the global header, where the
bitstream filter does not reach, hence `info=0`). Verified by the tests: no `title` / `comment` / `encoder` tags, no
`x264 - core`, `x265 (build`, `Lavc`, `Lavf<digits>` bytes in mp4 / mov / mkv / webm outputs. Off: `-map_metadata 0`
(`1` in the frame pipeline, where the source is the second input).

Known limits of stripping (not tags we write): Matroska / WebM require the MuxingApp and WritingApp elements, which
bit-exact mode fills with `Lavf` (no version); a stream that is copied (`codec: copy`, `audio: keep` with a fitting
codec) keeps its own bitstream bytes, including any encoder string another tool put inside the stream.

### 3.2 Codecs and the quality mapping

`quality` 0..100 -> rate-control value `round(a - b q)`, clamped to the encoder's range:

| codec | CPU encoder | CPU parameter | mapping | GPU encoder | GPU parameters |
|---|---|---|---|---|---|
| h264 | `libx264 -preset slow -bf 3` | `-crf` | `36 - 0.21 q` (q70 -> 21, q100 -> 15) | `h264_nvenc -preset p6 -tune hq` | `-rc vbr -b:v 0 -cq <v> -multipass fullres` |
| h265 | `libx265 -preset slow -x265-params log-level=error:info=0` | `-crf` | `38 - 0.22 q` (q70 -> 23) | `hevc_nvenc -preset p6 -tune hq` | as h264 |
| av1 | `libsvtav1 -preset 6` | `-crf` | `50 - 0.30 q` (q70 -> 29) | `av1_nvenc -preset p6` | `-rc vbr -b:v 0 -cq <v>` |
| vp9 | `libvpx-vp9 -row-mt 1 -cpu-used 2` | `-b:v 0 -crf` | `45 - 0.30 q` | — | — |
| prores | `prores_ks -vendor apl0` | `-profile:v` | q < 40 -> 1 (LT), q < 75 -> 2 (standard), else 3 (HQ) | — | — |
| copy | `-c:v copy` (no filters) | — | — | — | — |

Every encoder gets `-g <round(2 x output fps)>`; h265 in mp4 / mov gets `-tag:v hvc1`. `libsvtav1` prints its
banner unless `SVT_LOG=1`, which `procs.child_env()` sets for every child.

Audio in a video output: `keep` copies when the codec fits the container (mp4: aac, mp3; mov: aac, pcm_*; webm:
opus, vorbis; mkv: anything), else re-encodes: `aac -b:a <audio_bitrate_k>k`, in WebM `libopus -b:a ...`;
`aac` always re-encodes the same way; `none` and sources without audio give `-an`. GIF: no audio, `fps` capped at
15, palette chain `split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5`
after the scale, `-loop 0`.

Audio extraction (`format` mp3 / m4a / opus / flac / wav of a video item) runs the audio job of section 5.3 with
`bitrate_k = audio_bitrate_k`, `sample_rate` / `channels` `keep`, no loudness normalisation; a source without audio
-> "The file has no audio track.". Its `Plan.kind` is `extract`.

`encoder: auto` = gpu when `Capabilities.nvenc[codec]` is true, else cpu; `gpu` without NVENC for the codec runs on
the CPU (normalisation already turns it into `auto`). An NVENC encode that fails with a driver / session error
(`No capable devices`, `Cannot load nvcuda.dll`, `OpenEncodeSessionEx failed`, `No NVENC capable devices`,
`Cannot init CUDA`, `Driver does not support the required nvenc API version`, `Minimum required Nvidia driver`)
is planned again for the CPU and run once with stage `retrying`.

### 3.3 Size limit (video)

Budget: `bits = size_limit_mb x 1024 x 1024 x 8 x 0.96`. Audio bits = audio kbps x duration (re-encoded: the chosen
bitrate; copied: the source stream bitrate, or `0.1 x` the overall bitrate when unknown). Video kbps =
`floor((bits - audio bits) / duration / 1000)`, floored at 80; below 150 kbps for a source whose short side is
above 720 (and no upscale, no smaller fit chosen) the plan adds a 720p fit and records a note. Then:

- CPU h264 / h265: two passes, run in the job's cache folder (`cache/jobs/<job id>/`, stats files `x264pass*` /
  `x265pass.log*`): pass 1 `-pass 1 -an -f null -`, pass 2 `-pass 2 -b:v <v>k -maxrate <1.3 v>k -bufsize <2 v>k`
  (x265 uses `-x265-stats x265pass.log`); progress 0..0.5 / 0.5..1, stages `pass 1` / `pass 2`.
- libvpx-vp9: one pass `-b:v <v>k -maxrate <1.3 v>k -bufsize <2 v>k`.
- libsvtav1: one pass `-b:v <v>k` only: SVT-AV1 refuses `-maxrate` outside CRF mode ("Max Bitrate only supported
  with CRF mode"), so the corrective retry below is its only cap.
- NVENC: `-rc vbr -b:v <v>k -maxrate <1.2 v>k -bufsize <2 v>k -multipass fullres`, one pass.
- After the encode: if `size > limit`, one retry (stage `retrying`) with `v' = v x limit / size x 0.95` (floor
  40 kbps); still too big -> `error` "The size limit could not be met; try a lower resolution." and the output is
  deleted.
- GIF, prores and `copy` with a limit: `error` "This format cannot be limited in size." before anything runs
  (normalisation already turns `copy` + limit into `h264`). A source without a known duration: "The duration of
  the file is unknown, so the size limit cannot be applied."

### 3.4 Model upscale of a video (fsrcnn)

EDSR is not offered for video (`catalog.VIDEO_UPSCALE_METHODS`): measured at 0.04 fps for
640x360 -> 2x on an i7-14700KF (26 s per frame), so even a short clip would take hours. A video patch with `edsr`
is normalised to `fsrcnn`; the pipeline below still runs any `Model` and is used by the tests with FSRCNN.

`Plan.kind = "pipeline"` (stage `upscaling`), used for `2x` / `4x` with `upscale_method` fsrcnn or edsr:

```
decoder: ffmpeg -hide_banner -nostdin -v error -i <src> -map 0:v:0 -an -sn -dn
         -vf scale=in_color_matrix=<m>:flags=lanczos+accurate_rnd+full_chroma_int
         -fps_mode cfr -r <source fps fraction> -f rawvideo -pix_fmt bgr24 -
encoder: ffmpeg -hide_banner -v error -y -f rawvideo -pix_fmt bgr24 -s <W>x<H> -r <fps> -i - [-i <src> -map 0:v:0
         -map 1:a:0?] -vf scale=out_color_matrix=<m>:...,<unsharp>,format=yuv420p[,fps=N] <codec args>
         -colorspace <bt709|smpte170m> <audio args> <metadata> -f <muxer> <out>
```

`<m>` = the source's matrix (`bt709`, or `bt601` for smpte170m / bt470bg; untagged sources: bt709 from 1280 x 720
up, else bt601), used on both sides so colours do not shift. Frames (`w x h x 3` bytes) are read by a reader
thread, upscaled by `Model.upsample` on the job thread and written by a writer thread (queues of 3 frames each), so
decoder, model and encoder overlap. Both processes run in one Job Object; cancel is checked per frame and kills
both. Progress = frames / (`nb_frames` or `duration x fps`), capped at 0.99 until the encoder finishes; `speed` =
`<fps> fps`. The pipeline is 8-bit (bgr24): a 10-bit source comes out as 8-bit. A size limit uses the one-pass
arguments above (no two-pass over a pipe); the corrective retry reruns the whole pipeline. GIF output works through
the same pipeline (palette chain in the encoder).

Measured speeds are in section 8. EDSR on video is very slow on a CPU (well under 1 fps even at 640 x 360); the UI
should say so before such a job starts and rely on the ETA shown from the measured speed.

## 4. Super-resolution models

| Name | Scales | File | Source | Size | Licence |
|---|---|---|---|---|---|
| fsrcnn | 2, 4 | `FSRCNN_x2.pb`, `FSRCNN_x4.pb` | `https://github.com/Saafke/FSRCNN_Tensorflow/raw/master/models/` | 38 973 / 41 661 B | Apache-2.0 |
| edsr | 2, 4 | `EDSR_x2.pb`, `EDSR_x4.pb` | `https://github.com/Saafke/EDSR_Tensorflow/raw/master/models/` | 38 490 162 / 38 573 255 B | Apache-2.0 |

`cv2.dnn_superres.DnnSuperResImpl.create()`, `readModel(path)`, `setModel(name, scale)`, `upsample(bgr)`. FSRCNN
ships in `src/media_forge/models/` (the spec file must add `src/media_forge/models/*.pb` as data under
`media_forge/models`); EDSR is downloaded on first use into `<data>/models/` (`ensure_model`: item stage
`downloading model` with the download fraction as progress, `Capabilities.models.edsr = downloading`, `.part`
then replace, entry in `models.json`; a per-file lock keeps two jobs from downloading the same file). The SHA-256
of all four files is pinned in `upscale.MODEL_FILES` and listed in `models/MODELS.md`; a present file is hashed on
its first load in a process; a mismatch deletes a downloaded file and reports "The model file is damaged."; a
network failure reports "The model could not be downloaded." and sets `models.edsr = error` until the next status
refresh. `model_status` reports `ready` when the files of both scales are present with the pinned size. No network
while a model is present.

Tiles: frames larger than one tile are upscaled in overlapping tiles (input px): FSRCNN 1024 with 16 overlap, EDSR
512 with 32 overlap (EDSR's receptive field is larger than 16 px); the result is deterministic and within 0.5 grey
levels on average of the untiled output. One `Model` per job (OpenCV nets are not shared between threads).

Lanczos: OpenCV `resize(INTER_LANCZOS4)` for images, ffmpeg `flags=lanczos` for video. Image sharpen: unsharp mask
`addWeighted(img, 1 + a, GaussianBlur(img, sigma 1.0), -a, 0)` with `a` = 0.5 / 0.35 (Lanczos 2x / 4x) and 0.25 /
0.18 after a model; skipped when `sharpen` is off. Nothing here is generative; no metadata about the method is
written anywhere.

## 5. Image and audio jobs

### 5.1 Output naming

`catalog.output_path`: `<dir>\<stem><suffix>.<ext>`; `<dir>` = the source folder (`beside`) or `output_dir`
(`folder`); taken -> `<stem><suffix> (2).<ext>`, `(3)`, ...; a candidate equal to the source path is skipped (empty
suffix in folder mode). Jobs call it with `reserve=True`: the name is claimed by creating an empty file with
`O_EXCL`, so parallel jobs never collide; the job writes over it, and a failed or cancelled job deletes it. Output
extensions = the format id (`jpg`, `tiff`, `heic`, `m4a`, `opus`, ...).

### 5.2 Image pipeline

1. Open with Pillow, `load()`, keep `icc_profile`, XMP and EXIF (after `exif_transpose`, which resets the
   orientation), any open failure -> "Not a media file.".
2. Mode (`image.prepare`): 16-bit grey (`I;16`, `I`) stays 16-bit for png / tiff without a model upscale, else
   8-bit `L`; `1` -> `L`; `CMYK` -> sRGB through its ICC profile (`ImageCms`), ICC dropped; `P` / `PA` -> `RGBA` with
   transparency else `RGB`; `LA` -> `RGBA`; other modes -> `RGB`. `RGBA` is kept for png / webp / avif / tiff, else
   composited on white; `L` is kept for jpg / png / tiff / bmp, else `RGB`. A grey or CMYK profile is dropped when
   the colour model changes.
3. Scale: `0.5x` / `0.25x` with `Image.LANCZOS, reducing_gap=3`; `2x` / `4x` through `upscale.upscale_array`
   (Lanczos + sharpen, or a model on the colour channels; alpha resized with Lanczos and re-attached; grey images
   go through the model as BGR and back).
4. Save (`image.encode`, the image's `info` is emptied first so nothing rides along implicitly): jpg `quality`,
   `optimize`, `progressive`, `subsampling` 0 (4:4:4) at quality >= 90 else 2 (4:2:0) — when Pillow's
   buffer for an optimised progressive JPEG (about width x height bytes) is too small for a very detailed image,
   that one save falls back to baseline without Huffman optimisation; png `optimize=True, compress_level=9`;
   webp `quality, method=6`; avif `quality, speed=4`; tiff `compression="tiff_deflate"`; bmp none; heic `quality`
   (`pillow_heif`, RGB).
5. Metadata: `strip_metadata` on = no `exif`, `xmp`, comments or text chunks; `icc_profile` kept (not for bmp).
   Off = EXIF for jpg / png / webp / avif / tiff / heic and XMP for jpg / webp / avif are written back.
6. Size limit: the first save at `quality` is used when it fits. Lossy formats otherwise: quality search in
   `[min(10, quality), quality]` (the lowest value first, then binary search, at most 7 encodes) for the largest
   value under the cap; too big at the lowest quality -> downscale by `sqrt(cap / size_at_lowest) x 0.95` (never
   below 64 px on the short side) and search again, at most 4 rungs; then "The size limit is too small for this
   image.". Lossless formats (png / tiff / bmp) that do not fit at once -> "This format cannot be limited in
   size." (no `quantize` attempt: it changes the image, which a lossless choice should not do).
7. Progress: `analyzing` 0.05 / 0.1, `upscaling` 0.3 -> 0.8, `writing` 0.9 -> 1.0. Cancel is checked between the
   steps and inside the quality search. Two runs of the same image and settings give identical bytes (tested for
   png and jpg with Lanczos and FSRCNN at 2x and 4x).

### 5.3 Audio pipeline

`ffmpeg ... -i <src> -map 0:a:0 -vn -sn -dn [-af loudnorm=I=-16:TP=-1.5:LRA=11] [-ar <rate>] [-ac <n>] <codec>
[-b:a <k>k] <metadata> [-id3v2_version 0 -write_xing 0] [-movflags +faststart] -f <muxer> <out>`: mp3
`libmp3lame`, m4a `aac` (muxer `ipod`), opus `libopus` (max 256 kbps, always 48 kHz: libopus has no 44.1 kHz),
ogg `libvorbis`, flac `flac -compression_level 8`, wav `pcm_s16le`. With `normalize` and `sample_rate: keep` the
source rate is set explicitly (`loudnorm` resamples to 192 kHz internally). Stripped mp3 files have no ID3v2 tag
and no Xing / LAME info frame; libmp3lame itself writes `LAME3.100` into unused ancillary bits of some frames (more
of them for very simple signals), which ffmpeg cannot switch off. Size limit: `kbps = floor(limit_mb x 8192 x 0.97
/ duration)`, then the largest value of `[64 96 128 160 192 256 320]` `<= kbps` (opus <= 256); below 64 -> 64 with
a note; opus with a limit adds `-vbr constrained`; after the encode one retry at the next lower bitrate when the
file is still too big, then "The size limit could not be met at the lowest bitrate."; flac / wav with a limit ->
"This format cannot be limited in size.". Progress from `out_time_us / duration`, `speed` = `<x>x`.

## 6. Jobs and events

State machine: `probing` -> `ready` -> `queued` -> `running` -> `done` | `error`; `cancel` from `queued` or
`running` -> `ready` (progress 0, partial output deleted); `remove` from any state (a running job is cancelled and
dropped by its worker when it stops; `item_removed` is sent at once).

`start(ids)` (`JobQueue.start_items`) queues in list order: `null` = every `ready` item; explicit ids accept
`ready`, `done` (run again: a new numbered output through the usual naming, the old file stays) and `error` items
that have a probe (retry; a file that failed to probe cannot be started). Queuing resets `progress`, `stage`,
`eta`, `speed`, `output` and `error`.

`start_now(id)` (`JobQueue.start_now`, `Engine.start_now`) runs one item at once on an extra thread
(`jobs-now-<job id>`) that ends with its job, outside `parallel_jobs` and the image lane: a `queued` item leaves the
queue; `ready`, `done` and probed `error` items get the same reset as queuing and skip the `queued` state (one
`item` event with `running`). `False` for an unknown, `probing` or `running` item, a failed probe, or while the
queue is stopping. The job counts in `batch_done` like any other; `stop()` cancels and joins these threads too.

Workers: `parallel_jobs` general threads (1..3, changed at runtime by `set_settings`) take the first queued item of
any kind; one extra image-lane thread takes only image items, so a photo can run beside a video (at most
`parallel_jobs + 1` running from the queue, plus any `start_now` jobs). The runner (`Engine._run_job`) gets a snapshot of the item and a `JobContext`; it
reserves the output name, waits for ffmpeg when the kind needs it (cancel still works while waiting), runs the
job, describes the output (`ffprobe` of the result for width, height and duration; image dimensions from the
pipeline; `ratio` = output / source size), and in `finally` deletes the output when the job did not finish and
always removes `cache/jobs/<job id>/`.

Events: every transition emits `item`; `report()` updates are coalesced to at most one `item` event per 150 ms per
item (a stage change is sent at once; a flusher thread sends the last pending update). Events go out under one
emit lock with a snapshot taken inside it, so an item event never overtakes a newer one. `eta` = elapsed x (1 - p) /
p once p > 0.02. `batch_done {done, failed, cancelled}` is sent when nothing is queued or running and at least one
item finished, failed or was cancelled (queued or running) since the last `batch_done`.

Errors: `UserError` messages go to `Item.error` as they are; ffmpeg failures are mapped (`ffjob.error_message`): no
space left -> "There is not enough space on the disk."; permission denied / file in use -> "The output file cannot
be written; it may be open in another program."; invalid data / moov atom missing -> "The file is damaged or uses
an unsupported format."; unknown encoder / tag not supported by the container -> "The codec is not supported for
this format."; else "ffmpeg failed: <last stderr line>." trimmed to 160 characters. The command line, exit code and
the last 200 stderr lines go to `logs/jobs/<job id>.log`. Any other exception is logged with its traceback and
shown as "The conversion failed; details are in the log.".

## 7. Engine

`Engine.start()`: `paths.ensure()`, `clear_cache()`, load settings, start the workers and a probe pool (3 threads),
and discover the tools on a background thread: `find_ffmpeg` (bin, then PATH); none and `allow_download` ->
`Capabilities.ffmpeg = downloading`, `download_ffmpeg` (zip verified against the release's `checksums.sha256`,
`bin/ffmpeg.exe` + `bin/ffprobe.exe` extracted through `.part` files, `bin/versions.json`), failure -> toast
"ffmpeg could not be downloaded; video and audio files cannot be processed." and `ffmpeg = missing`. Then
`probe_capabilities` (`-version`, `-encoders`, one-frame NVENC test per codec in parallel, 10 s timeout each,
`nvidia-smi` name, AVIF from `PIL.features`, HEIF from the `pillow_heif` import), `models` from `model_status`,
`capabilities` event, and a re-normalisation of the stored defaults and of every idle item with the probed
capabilities (`settings` / `item` events when something changed). Until the probe is done, normalisation runs with
`caps = None` (static NVENC table, so a stored `gpu` choice survives start-up).

`add_files(paths)` deduplicates by normalised absolute path (toast "The file is already in the list." or "<n>
files are already in the list."), creates items with the defaults of the kind by extension (status `probing`) and
probes them on the pool; when the probe changes the kind, the item gets the defaults of the new kind. `state()`
returns `version`, `settings`, `items`, `capabilities`, `options` (the shell adds `update` and `window`).
`set_settings` emits `settings` on success and also on refusal (the old values) before re-raising. Helpers for the
CLI and tests: `item(id)`, `plan(id)` (the command lines as text), `wait_tools`, `wait_probed`, `wait_idle`.
`stop()` cancels every job, joins the workers, stops the probe pool and saves the settings.

## 8. Tests and measurements

`tests/test_core_*.py` + `tests/core_support.py` (unittest). Scratch root = `%MEDIA_FORGE_HOME%\tests` when set,
else `<temp>\media-forge-tests\tests`; one data folder and output folder per test class; test media generated
once per root with ffmpeg lavfi (`testsrc2`, `sine`, `anoisesrc`) and Pillow. Coverage: catalog normalisation (each
rule of `BRIDGE_API.md` 3.4) and options, output naming and reservation, settings validation / persistence /
broken file, probe of every kind with thumbnails (rotation, EXIF orientation, alpha, 16-bit, animated vs single
GIF, audio-only mp4, not-media, missing file), tools (version parsing, sums, verified ffmpeg install from a local
HTTP server, checksum mismatch, capability probe) and procs (capture, timeout, cancel kills a grandchild), video
per codec (h264, h265, av1, vp9, prores, copy; NVENC h264 / h265 / av1 skip without a GPU), metadata absence,
GIF, audio extraction, Lanczos 2x, fit and crop, fps, rotation, FSRCNN 2x and 4x (GIF) through the pipeline,
two-pass and one-pass size limits (h264, h265, vp9, NVENC) under the cap, refused limits; images per format with
metadata stripping, ICC kept, alpha, modes, downscale, size cap (quality search and downscale ladder), Lanczos and
FSRCNN 2x / 4x with identical bytes on a second run, cancel; audio per format, size-limited mp3 / opus, lossless
refusal, loudness normalisation; upscale model hashes, tiles, EDSR download / damage / failure paths with a stand-in
file served locally (the real EDSR test runs when the files are in the data folder); engine flow (events,
throttling, cancel deletes the partial file, refusal while running, apply to kind, remove, clear, retry after an
error, run again after done -> `(2)`, missing output folder), queue lanes and parallelism, the CLI.

Measured speeds, 2026-10-21, Intel Core i7-14700KF (28 threads), OpenCV 5.0 CPU build, RTX 4070 Ti SUPER for
NVENC; 640 x 360 source, 2x to 1280 x 720 (the encoder runs in parallel with the model):

| Method | Encoder | Speed | Note |
|---|---|---|---|
| Lanczos (ffmpeg filter) | libx264 slow | about 7x real time (about 200 fps) | 3 s clip |
| FSRCNN x2 pipeline | libx264 slow | 8.6 fps | 90 frames in 10.5 s; the model alone needs 0.10 s per frame |
| FSRCNN x2 pipeline | h264_nvenc | 8.3 fps | model-bound, the encoder does not matter |
| EDSR x2 pipeline | h264_nvenc | 0.04 fps (about 26 s per frame) | 30 frames in 776 s; the model alone needs 4.8 s per 320 x 180 frame |

The OpenCL target of OpenCV's dnn (NVIDIA device) was tried: FSRCNN 5x slower, EDSR about 2x faster but with
kernel build errors for some layers and results that differ from the CPU path; the core stays on the CPU target.

## 9. Deviations from and additions to BRIDGE_API.md

- `Item.thumb` is a JPEG data URL, except a PNG data URL for audio waveforms (transparent background) and for
  images with transparency.
- `Item.probe.format` for video / audio is the first two names of ffprobe's `format_name` (`mov,mp4`).
- `start(ids)` with explicit ids also accepts `done` and `error` items (as the updated contract says); an item
  whose probe failed cannot be started.
- A GIF output with `size_limit_mb` is refused ("This format cannot be limited in size."), like prores.
- Image formats png / tiff / bmp with a limit succeed when the first save fits; otherwise the refusal above.
- Audio-kind normalisation clamps `bitrate_k` to 256 when `format` is `opus`; a video item's opus audio in WebM is
  not clamped (libopus accepts 320 kbps stereo).
- `encoder: gpu` before the capability probe is kept for h264 / h265 / av1 (static table); it is re-normalised when
  the probe finishes.
- For formats without codecs (gif, audio-only) the `codec` value is kept unchanged (it does not apply).
- Settings: an invalid suffix is refused with "The suffix contains characters that are not allowed in file names."
  or "The suffix can be at most 24 characters long."; `output_dir` is checked only when the resulting mode is
  `folder` and the patch touches `output_mode` or `output_dir`; a stored folder that disappeared is kept and the
  job reports "The output folder does not exist.".
- `open_when_done` and `sound_when_done` are stored and validated by the core; acting on them (Explorer, sound) is
  the shell's job (it sees `batch_done` and the `done` items).
