# Media Forge user guide

Everything about using the app. Installation and the first steps are in the [README](../README.md).

## Usage

1. Drop files on the window, press **Choose files** or `Ctrl+O`. Each file is probed in the background and shows
   its thumbnail, dimensions, duration, codec and size.
2. Click a file to open its settings: format and codec, encoder, scale and upscale method, quality or size limit,
   frame rate, audio, metadata. The line under the file name shows what will be written.
3. Press **Start** (`Ctrl+Enter`). Each card shows the stage, progress, speed and ETA; a running job can be
   cancelled, a finished or failed one run again. A cancelled or failed job deletes its unfinished output; when
   another program (a virus scanner, a thumbnail viewer) still holds that file after about 3 seconds, the file
   stays and the log names it.
4. **Show in folder** reveals the output in Explorer; **Clear done** removes finished files from the list.

Outputs are written next to the sources as `<name>_forge.<ext>` unless **Output** in Settings names one folder.
A dropped folder brings the media files of all its subfolders; with one output folder, the outputs keep the same
subfolders below it (a card's `DCIM\100` becomes `<output folder>\DCIM\100`). An output folder that lies inside
the dropped folder is left out, so dropping the same folder again does not convert the outputs. A folder walk stops
at 1000 media files (or 100,000 entries read) and says so; a file given directly is always added.

Files and folders also reach the list from Explorer: **Open with** > Media Forge, **Send to** (a shortcut to
`MediaForge.exe` in the `shell:sendto` folder), or a file dropped on `MediaForge.exe` itself. While Media Forge is
open, such a start adds its files to the open window instead of opening a second one. When that window is just
closing, the start waits for it for up to 10 seconds and then opens its own window with the files. A message says
when the files could not be added: the open window did not answer in that time, or it runs as another user, or
without administrator rights while the start has them.

## The window

Drag any edge or corner to resize the window, the title bar to move it. A taller window shows more files at once;
a wider one widens the list up to a comfortable reading width and keeps it centred beyond that. The window cannot
get smaller than its smallest layout and is never maximized or made full screen (minimize and close stay). Its size
and position are remembered and restored at the next start; when the screen it was on is no longer connected, it
opens centred on the main screen. Moving it to a screen with another display scaling keeps its size and keeps the
text sharp.

## Keyboard

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
| **Theme** | Dusk, Nova (default), Frost or Bloom: the background colour family and the accent of the window. |
| **Scale** | 100 (default), 125, 150, 175 or 200 %: the size of the whole window and its text, on top of the Windows display scaling. A change resizes the window by the same ratio around its centre, so its proportions stay; a step whose smallest window does not fit the screen is dimmed. When the chosen step stops fitting (another display, another display scaling), the largest step that fits is applied and shown, and the chosen one returns on a screen with room for it. |
| **Output** | Next to the source (`<name>_forge.<ext>`) or one folder of your choice. |
| **Suffix** | The part added to the file name, `_forge` by default; may be empty when outputs go to a folder. |
| **Keep file dates** | On by default: each output gets the modified and created dates of its source, so files sorted by date stay in order. Off: the output carries the time it was written. |
| **Parallel jobs** | 1 to 3 files at a time. |
| **Open the folder when done** | Opens Explorer with the last output selected after a batch. |
| **Sound when done** | A short chime when a batch finishes. |
| **Defaults for new files** | The per-kind settings every new video, photo or audio file starts with. |
| **Download updates automatically** | On by default: a new release is downloaded in the background; turn it off to download only on **Download**. Installing always needs **Restart**. |

## Formats and codecs

| Kind | Output formats | Codecs and options |
|---|---|---|
| Video | MP4, MKV, WebM, MOV, GIF, animated WebP, animated PNG; audio extraction to MP3, M4A, Opus, FLAC, WAV | H.264, H.265 / HEVC, AV1 (CPU or NVENC), VP9, ProRes, stream copy; scale original / 2x / 4x / fit 1080p, 720p, 480p; frame rate keep / 24 / 30 / 60; audio keep / AAC 96 to 320 kbps / none; GIF is capped at 15 fps |
| Photo | JPEG, PNG, WebP, AVIF, TIFF, BMP, HEIC | quality 0 to 100 for the lossy formats (PNG is optimised instead); scale original / 2x / 4x / ½ / ¼ |
| Audio | MP3, M4A (AAC), Opus, OGG (Vorbis), FLAC, WAV | 64 to 320 kbps (Opus up to 256; FLAC and WAV are lossless, and WAV keeps the source's 16-bit, 24-bit, 32-bit or floating-point samples); sample rate keep / 44.1 / 48 kHz; mono / stereo; loudness normalisation to -16 LUFS |

A video's formats show as two rows with one choice between them: **Format** holds the videos and the animated
images, **Audio only** the outputs that keep just the sound. For a video without an audio track the Audio only row
is dimmed, and its tooltip says that the file has no audio track.

An animated GIF, WebP or PNG is listed as a video, so it keeps every frame: it can become an MP4 or any other
video, or stay animated as GIF, animated WebP or animated PNG (animated PNG is lossless). In the Format row these
two read WebP and APNG, and their tooltips say Animated WebP and Animated PNG; likewise the audio formats M4A and
OGG carry AAC and Vorbis. Animated WebP and PNG outputs keep each frame's time and the number of plays; a GIF
output keeps the number of plays and the total time, and a video the total time. Animated outputs carry no audio
and cannot be held to a size limit.

Upscale methods for 2x / 4x: **Lanczos** (resampling with a light sharpen), **FSRCNN** (small deterministic
super-resolution network, bundled) and, for photos, **EDSR** (larger network, downloaded once, 38 MB per scale,
slow on a CPU, best detail). A video patch that asks for EDSR is written with FSRCNN instead. The same input
always gives the same output; nothing is painted in from noise or text.

**Remove metadata** is on per file by default: no EXIF, XMP, IPTC, chapters, encoder tags or provenance manifests
of any kind; only the ICC colour profile is kept. Off copies the source metadata as far as the target format
allows.

## Size limit

The limit is a hard cap on the output file, set per file in MB:

- **Video:** the bitrate is computed from the duration and the audio share, encoded in two passes (or NVENC VBR
  with a cap), and the job retries once with a lower target when the result still exceeds the cap. When the audio
  would leave the picture too little room (a large lossless track kept as it is), the audio is re-encoded at a
  lower bitrate instead and the video bitrate is computed again with the room that frees. When the retry finds
  that a kept track was larger than planned, it re-encodes that track at a bitrate below its own; otherwise the
  track stays as it is and only the video is lowered. A limit too small for the sound and the smallest picture ends
  with an error before anything is encoded.
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

The app checks GitHub Releases on every launch in the background and then every 5 minutes while it runs. A failed
check is retried later with a growing wait of up to an hour, and nothing is checked without a network connection.
A new version opens a sheet with its release notes, size and date; **Install** fetches it (the app does it on its
own unless **Download updates automatically** is turned off), verifies the byte size and the SHA-256 against the
release's `SHA256SUMS`, and restarts the app once the file is verified; **Later** closes the sheet until you click
the version label in the title bar. A version downloaded in the background still installs only on Restart, and it
stays ready when a later check finds no network. Every newer version, a new major version included, is offered and
installed this way. The downloaded exe waits for the app to exit, keeps the old file as `MediaForge.exe.old` until
the new one has started, and relaunches it. If anything fails the current exe stays untouched. Every release ships two assets:
`MediaForge.exe` and `SHA256SUMS`. Release notes: [`releases/`](releases/).
