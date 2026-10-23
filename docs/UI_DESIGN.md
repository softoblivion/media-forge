# UI design — Liquid Glass for Media Forge

Scope: everything under `src/media_forge/ui/`. The JS <-> Python contract is `docs/BRIDGE_API.md`;
this document describes the page that codes against the shared design system and where Media Forge differs. The
design system itself (tokens, fonts, themes, material, buttons, motion template, controls, overlays, toasts, title
bar, background, the version pill with the update popover, the theme row) is the shared Liquid UI kit, vendored at
`src/media_forge/ui/vendor/liquid_ui/` (version in its `VENDORED.txt`, 3.0.0 since Media Forge 2.0.0); the kit's own
documentation is cited below as "the kit, section N". This document
records what Media Forge uses, its own screen and where it differs.

## 1. Sources

| Part | From |
|---|---|
| `vendor/liquid_ui/css/` tokens, themes, base, scene, glass, components | The kit, unchanged (synced copy; never edited here). Fonts in `vendor/liquid_ui/fonts/`, named only in the kit's `tokens.css`. |
| `vendor/liquid_ui/js/` motion, glass, overlay, controls, toast, background, theme, icons, format, update | The kit, unchanged: springs and the row / menu entrances, lenses, sheets and popovers, Segmented / Switch / Stepper / Select / Slider, toasts, the scene, the four themes with `applyTheme` and the theme row, line icons, en-US formatting, `mountUpdateControl` (version pill, update popover, update toasts, the "Updates" rows of the settings sheet). |
| `css/app.css`, `js/*.js`, `assets/icon.svg` | Media Forge: screen layout, file cards, settings panels, footer, empty state, drop veil, status banner, settings sheet content, store, bridge wrapper, mock, app glyph, app tokens (kind colours, footer height), the persistence of the chosen theme. |
| Apple, WWDC25 "Meet Liquid Glass"; kube.io refraction article | Through the kit: lensing rather than scattering, materialize by ramping the lensing, controls lift into glass only while touched, tint only the primary action, scroll edge effect instead of hard clipping. |

The kit started as this app's own UI (which took its material, springs and several controls from Media Saver); it
became a shared kit without a visual change. Kit 3.0.0 (the design pass of 2026-10-21) went the other way: Media
Forge's Start pill became the kit's hero pill and its buttons the model of the kit's button system, and the app
dropped its own scene colours, button rules and accent tokens for the kit's themes.

## 2. Material — the kit, section 5

`Lens` presets used by this page: `pill` for the Start pill (the hero), `capsule` for the version pill, the window
cluster, the footer counters capsule and the toasts, `panel` for the empty-state drop target, `sheet` for the
settings sheet, the update popover and the drop veil, `droplet` for segmented indicators and switch thumbs while
pressed. `.glass-lite` for every file card (any number), the FFmpeg banner and the menus.

Rules the page keeps (the kit, section 5.3):

- Refracting surfaces at once: steady 4 (version pill, window cluster, counters capsule, Start) or 3 + the empty-state
  panel when the list is empty; plus up to 3 toasts, one sheet or popover, the drop veil while dragging, and one
  droplet while a control is pressed. Never more than about 12.
- No glass on glass: buttons inside the window cluster, the counters capsule, the sheet and the cards are the kit's
  plates or fills; the droplets lift into glass only while pressed.
- Backdrop roots: no `opacity < 1`, `filter`, `mask` or `clip-path` on an ancestor of a lens or a glass-lite card. The
  drop veil therefore fades its dim layer and its lens separately, never their parent; the footer menu lives outside
  the counters capsule; entrance animations fade only elements that are lenses themselves. Masks are used only on
  elements whose descendants are meant to see their own content: the settings scroller and overflowing chip rows.
- Tint only the primary action: Start while it can start (accent glass), Done (mint), the empty state's "Choose
  files", and Download / Restart to update in the update panel.
- Legibility: white ink in alpha steps, a 1 px text shadow on lenses, `--well` under inputs and long text.
- `prefers-reduced-transparency`: near-opaque tints, glass-lite without blur. `prefers-contrast: more`: solid rims.

## 3. Tokens and themes — the kit, sections 3 and 4; app `css/app.css`

Media Forge overrides none of the kit's identity tokens: the theme sets them. `css/app.css` adds only the app
tokens, the kind colours `--k-video #7fa8ff`, `--k-image #5fe3cf`, `--k-audio #bb9cff` (chips, plan arrow, the
slider fill of a panel, the tiles of the empty state) and `--foot-h 56px` (the footer and the hero pill height),
and uses the kit's semantic colours (`--mint` done, `--amber` warnings, `--rose` errors and the trash button) and
accent tokens (`--accent`, `--accent-2`, `--accent-ink`, `--btn-glow`, `--focus-ring`, the scene tokens) where the
app once had its own blues: the running card's liquid fill, the percent, the drop veil and its ripple, the size
limit field's focus ring, the scroll edges (built from `--bg-top` / `--bg-bottom`).

Themes: the kit's four (`dusk`, `nova`, `frost`, `bloom`; a hue family for the scene and a contrast accent). Media
Forge's default is **Nova** (violet hue `#6a3df0`, ember accent `#ff8a4a`). The choice is the global setting
`theme` (BRIDGE_API 3.5, normalised by the shell, unknown names fall back to `nova`), chosen in the Appearance group
of the settings sheet (the kit's `mountThemeControl` row; section 6.1). Applying and persisting (`js/main.js`):
`index.html` carries `data-theme="nova"` and a tiny inline script that reads `localStorage['media-forge.theme']`
and sets the attribute before the first paint when it is one of the four names (no flash of the wrong theme before
the bridge answers); `main.js` calls the kit's `applyTheme(settings.theme)` and writes that key on every confirmed
settings object (the `State`, the `settings` event, the sheet's own saves, the update control's saves), so the
attribute always ends on the shell's value. `applyTheme` also retints the scene (section 5).

Fonts and type roles, radii, spacing, shadows, layout (`--gutter`, `--titlebar-h`): the kit, section 3. Every
number that changes in place is `tabular-nums`.

## 4. Buttons and motion — the kit, sections 6 and 8

Every button of the page is the kit's `.btn` or `.icon-btn` (sizes `-l` / default / `-s` / `-xs`, kinds plain /
ghost / tinted / danger) placed by the app and never resized or refilled: the empty state's "Choose files"
(`.btn.btn-tinted.btn-l`), the state-line actions (`.btn.btn-xs`), the drawer's "Apply to all" (`.btn.btn-s`) and
"Remove" (`.btn-s.btn-ghost.btn-danger`), the Clear split button and Stop (`.btn-s.btn-ghost`), the footer's add
button (`.icon-btn.icon-btn-l.icon-btn-plate`), the card's chevron (`.icon-btn`), its trash button
(`.icon-btn.icon-btn-s`, rose ink and a rose fill on hover and press, the one colour the app adds) and the running
card's cancel (`.icon-btn.icon-btn-s`). Every button gets `gel()`.

The Start pill is the kit's hero pill (`.hero`, section 6.1 of the kit; `id="btn-start"`): `js/footer.js` builds
its layers, sets `data-state` (`empty`, `ready`, `progress`, `done`), `--p` (the batch level, `.pm-instant` when
entering progress) and springs its width to the content (min 148 px, 176 px while running, max 46 % of the
footer); `app.css` only sets `--hero-h: var(--foot-h)` and the width on `#btn-start`. Copy: "Start" / "Start N
files"; progress "k of n" on the left and the speed (or the batch percentage) on the right; "Done" for 2.6 s.

Motion follows the kit's template (section 8) and adds no spring or duration of its own:

| Element | Media Forge use |
|---|---|
| List rows | `enterRow(card, index)` for every card added (`items_added`, `pick_files`, `add_files`; the stagger per index); `leaveRow(card)` on `item_removed`: fade and shrink, then the height and the card's own bottom margin (the list spacing, so no flex gap is left behind) collapse and the cards below move up |
| Drawer | height 0 -> content with `SPRINGS.expand` (WAAPI, `springEasing`), the panel fades in from 8 px above with `smooth` after one stagger; collapse `sheetClose`, then the panel is destroyed (its droplet lenses go with it). One card is open at a time; after opening the list scrolls just enough to show the panel above the footer |
| Chevron | `rotate` over `--d-4` with `--ease-spring` |
| Footer | rises with `bouncy` when the first files arrive (lenses materialise); the Clear menu `fadeIn` / `fadeOut` |
| Hero width | `bouncy`; the layers cross-fade per the kit |
| Liquid levels | the kit's rule: `translateX` over 0.9 s `cubic-bezier(0.22, 0.8, 0.3, 1)`, `.is-instant` / `.pm-instant` for a reset; the running card's fill is the theme's accent, done flashes mint and fades (1.4 s) |
| CSS transitions | `--d-1` hover fills, `--d-2` state fades (trash button, clear button of the size field), `--d-3` card tint and locked panels, `--d-4` the scroll edge and the liquid layer's opacity |
| Decorative loops | keep their own periods: the thumbnail sheen and skeleton while probing, the liquid sheen while running, the bobbing tiles of the empty state, the drop veil's tray and ripple |
| Sheets, popovers, toasts, controls | the kit |

`prefers-reduced-motion`: springs critically damped and short, no CSS loops (scene, sheens, bobbing, pulse), no
row or drawer animation, the speck canvas stops.

## 5. Background — the kit, section 7

`js/main.js` calls `initBackground(#scene)` with no options: the kit's `deep` treatment reads the theme tokens of the root (`--bg-top / mid / bottom`, `--scene-hue`, `--scene-accent`,
`--scene-line`) and builds the base gradient, two soft fields of the hue, one small field of the accent, the
contour and caustic lines, the floor pool, the specks, the light sweep, vignette and grain. `applyTheme()` calls
`retint()`, so a theme switch in the settings sheet recolours the scene in place. The app's own four colour fields
of 1.x are gone.

Pausing and cost: the kit's rules (hidden document, unfocused window through the bridge's `window` event, 90 s
without input, reduced motion); the page's own loops hold with `html.is-still`.

## 6. Components and files

| File | Content |
|---|---|
| `index.html` | Static layout: `data-theme` and the theme bootstrap script, scene, lens defs, list scroller (FFmpeg banner, list, empty state), scroll edges, title bar (drag region, brand, version pill, window cluster), footer (counters capsule, the hero Start pill), overlay hosts (update popover, settings sheet), drop veil, toasts. Links the kit's six stylesheets (tokens, themes, base, scene, glass, components), then `css/app.css`. |
| `vendor/liquid_ui/` | The kit (section 1): `css/`, `js/`, `fonts/`, `VENDORED.txt`. |
| `css/app.css` | App tokens and the screen: scroll area and edges, status banner, cards, drawer and panels, footer and the hero's height / width, empty state, drop veil, size limit field, settings sheet content (output path, defaults panel, engine rows, about line). No button geometry or fill, no identity colours, no durations of its own. |
| `assets/icon.svg` | App glyph (isometric block with an ember spark). |
| `js/main.js` | Boot (theme sync, scene, light, toasts), title bar, empty state, FFmpeg banner, the kit's update control (`mountUpdateControl`, store `update`), paste of copied paths (`add_files`), keyboard, entrance, `toast` / `window` / `capabilities` events, error reporting through `log`. |
| `js/api.js` | `window.mediaForge.dispatch` (registered at import, before `ready()`), event queue until `goLive()`, pywebview wait (1.5 s) or mock, `call()`, `on()`, `reportError()`. |
| `js/mock.js` | Full mock of BRIDGE_API (section 7). |
| `js/store.js` | Confirmed state (version, settings, caps, options, update, items) and watchers; `gpuFor(codec)`. |
| `js/list.js` | File cards, state lines, the trash button of a collapsed card, drawer with the settings panel, Apply to all, Remove, Retry, Show in folder, Open, Start now, Cancel; events `item`, `items_added`, `item_removed`; the kit's `enterRow` / `leaveRow`. |
| `js/panels.js` | `SettingsPanel` per kind (video / image / audio) and `planSummary`; used by cards and by the settings defaults. |
| `js/footer.js` | Counters capsule (add, counts, size change, Clear split menu, Stop), the hero Start pill's states and width, `batch_done`. |
| `js/settings.js` | Settings sheet: appearance (the kit's theme row), output, processing, defaults per kind, engine (capabilities); the "Updates" group is filled by the kit's `update.mountSettings()`. |
| `js/dropzone.js` | Window-wide drop veil and ripple; mock drops. |
| `js/sound.js` | Finish chime. |
| `js/icons.js`, `js/format.js` | The kit's `icon()` plus `KIND_ICON` and the app glyph; the kit's formatting plus file counts, codec names, channels, sample rates, kind labels. |

### 6.1 Layout (3:4, 540 x 720 to 630 x 840 and up)

- Title bar (50 px, `pywebview-drag-region` on a full-size layer behind the items, on the brand and on the spacer;
  buttons are siblings, never inside a drag element): glyph + "Media Forge", the version pill (mono `v2.0.0`, a dot
  for `available` / `downloading` (pulsing) / `ready` (mint)), spacer, the window cluster capsule: gear | minimize,
  close. No maximize (aspect lock).
- The list scrolls under the title bar and the footer; soft scroll edges (blur + gradient, masked) sit above it, so
  the title-bar and footer lenses refract the cards passing under them. The scrollbar track is inset to the free area.
- Empty state (no items): a `panel` lens with three fanned tiles in the kind colours (video, photo, waveform), "Drop
  video, photo or audio files", "Compress, upscale or convert them. Each file keeps its own settings.", tinted
  "Choose files", the Ctrl+O hint and a line of common formats. The footer is hidden while the list is empty and
  rises with a spring when the first files arrive.
- Card: 64 x 48 thumbnail (56 x 44 below 600 px), name, kind chip (Video / Photo / Audio; none for a file that could
  not be read), probe line (`1920×1080 · 1:12 · H.264 · 46.8 MB`; audio `3:41 · AAC · 48 kHz · Stereo · 8.4 MB`), and
  one state line. On the right of the head: a trash button (the kit's small glyph button, `--rose` icon, rose fill
  on hover and press, gel press, tooltip "Remove"; `remove_item(id)`, which also cancels a running file) while the
  card is collapsed (hidden, its space kept, while the panel is open: the panel has "Remove"), then the chevron.

  | Status | State line | Actions |
  |---|---|---|
  | probing | skeleton bar, "Reading file…" (thumbnail sheen) | |
  | ready | arrow + plan summary ("MP4 · H.264 · Quality 70 · GPU", "MKV · H.264 · ≤ 25 MB · 2×", "MP3 · 192 kbps") | |
  | queued | "Queued" | Start now (`start_now(id)`: runs the file at once on an extra worker beside the running ones, ignoring "Files at the same time"; a `false` answer shows "The file could not be started."), Cancel |
  | running | stage ("Encoding", "Pass 1 of 2", "Upscaling", "Downloading model", "Retrying to fit the size limit", …), percent, speed · ETA, liquid fill | × (Cancel) |
  | done | `9.1 MB · 19 %` (+ the new resolution when it changed) | Show in folder, Open, Run again (`start([id])`: a new numbered output, the old file stays); the actions wrap to their own line when the row is too narrow |
  | error | the message (2 lines) | Retry (`start([id])`, only when the file was read) |

- Drawer (one open card): the kind's settings panel, then "Apply to all video / photos / audio" (disabled when no other
  idle file of the kind exists) and "Remove". The panel is locked (inert, dimmed, with a note) while the file is
  probing, queued or running; a done or failed file stays editable for Run again / Retry.
- Footer: counters capsule [+ add files] ["7 files" / "2 done · 1 failed · 3 in queue · −84 MB"] [Clear done | ▾ menu:
  Clear done, Clear failed, Clear all except running, Clear all] — while files run the Clear split becomes "Stop"
  (`cancel_all`); then the hero Start pill.
- Settings sheet (modal, grows from the gear): Appearance (Theme: Dusk / Nova / Frost / Bloom, the kit's row;
  applied at once, saved as `theme`, a refusal shows the sentence as a toast and restores the confirmed theme),
  Output (Save to: Beside the source / One folder; folder path + Choose… + open; File name suffix with a live
  example), Processing (Files at the same time 1-3 stepper, Open the folder when done, Sound when done), Defaults
  for new files (Video / Photo / Audio + the same panel), Engine (FFmpeg version and status, GPU and NVENC codecs,
  FSRCNN, EDSR, AVIF / HEIC), Updates (status line + Check for updates / Download / Restart to update, Download
  updates automatically, on by default; the check period is fixed and has no control), the version line.

### 6.2 Settings panels (BRIDGE_API 3.3)

| Kind | Rows (visibility) |
|---|---|
| video | Format (scroll chips, the ten Options labels); Codec (formats with codecs; rebuilt when the format changes); Encoder Auto / CPU / GPU (formats with codecs, not for Copy stream; GPU disabled when `gpuFor(codec)` is false, hint "Uses the GPU / CPU for this codec" under Auto); Scale (not for audio-only formats); Upscale method (2× / 4× only, with the Options hint, or the model status: downloading / error / missing bundle); Quality slider (not for audio-only; disabled with "Set by the size limit" or "Not used when the stream is copied"); Size limit (MB, decimals, clear button; hint per case; warning for FLAC / WAV); Frame rate + Audio (side by side; Audio hidden for GIF; GIF hint "capped at 15 fps"); Audio bitrate (Audio = AAC, or a lossy audio-only format); Sharpen after upscale (2× / 4×); Remove metadata. |
| image | Format (HEIC only when listed; AVIF disabled when `Capabilities.avif` is false); Quality (disabled for lossless formats or with a size limit); Size limit; Scale; Upscale method and Sharpen (2× / 4×); Remove metadata ("EXIF, XMP and IPTC; the colour profile stays"). |
| audio | Format; Bitrate (lossy formats; hint for Opus above 256 and for a size limit); Size limit (warning for FLAC / WAV); Sample rate + Channels; Normalise loudness (EBU R128, −16 LUFS); Remove metadata. |

The slider fill of a panel is the kind colour (`--k-accent` from `--k-video` / `--k-image` / `--k-audio`), not the
theme's accent, so the three panels read as the three kinds in every theme.

Every change is a one-key patch: `set_item_settings(id, patch)` for a card, `set_settings({defaults: {<kind>:
patch}})` for the defaults. The panel applies the change locally until the answer arrives, then shows the returned
(normalised) settings; a refusal shows the sentence as a toast and restores the last confirmed values. A sequence
number drops stale answers; item events do not move a panel while its own patch is in flight.

### 6.3 Updates (UpdateStatus.stage)

The version pill, the update popover, the update toasts and the "Updates" rows of the settings sheet are the kit's
update control (`mountUpdateControl`; stages, copy and behaviour in the kit, section 10; its bridge contract,
section 11, matches BRIDGE_API sections 2, 3.5, 3.7, 3.8 and 4). Media Forge passes: product "Media Forge", the
State's version, update status and settings, the restart warning "Files in progress stop
when the app restarts" (shown with "Restart anyway" while files are queued or running), `onStatus` (store `update`)
and `onSettings` (store `settings`); `js/main.js` forwards every confirmed `settings` to `setSettings`.

In short: idle / up_to_date "Check for updates"; checking with an indeterminate bar; available with Installed /
Latest rows, release notes and a tinted "Download"; downloading with a bar, percent and sizes; ready with a tinted
"Restart to update" (disabled when `can_apply` is false); error with the message and "Try again". The notice of a
newer version is the kit's update sheet (kit 3.1.0, section 10.1): it opens by itself the first time a version is reported, with the title "Media Forge X.Y.Z", the installed
version, size and date, the release notes, a progress bar while the shell downloads (ARCHITECTURE 5.7) and the
buttons Later / Install (then "Downloading… 42%", "Restart to update"); Later closes it until the pill is clicked.
`ready` raises one sticky toast with "Restart" when the sheet is closed at that moment. The page never downloads or
restarts on its own. Release-note links open in the default browser through `open_url`.

### 6.4 Keyboard

Ctrl+O choose files; Ctrl+Enter start; Delete removes the focused card (focus moves to the next one); Enter / Space on
a card opens or closes its panel; Up / Down move between cards; Esc closes the top overlay or menu, else closes the
open card; arrows move segmented selections and the theme choice; Ctrl+V of copied Windows paths (outside inputs)
adds them through `add_files`. Visible `:focus-visible` rings everywhere (the theme's `--focus-ring`).

## 7. Runtime notes

- ES modules do not load from `file://`: the shell serves the folder over `http://127.0.0.1:<port>/`. Dev preview:
  any static server on the `ui` folder, open `index.html?mock=1`. The mock is used with `?mock=1` or when pywebview
  does not appear within 1.5 s.
- Mock URL flags: `items=N` (N ready files), `demo=1` (one file per state: done, failed, FLAC with a size limit,
  running, unreadable, ready), `fast=1` (jobs 4× faster), `update=available|uptodate|error` (launch check result),
  `nvenc=0` (no NVIDIA GPU: GPU options disabled), `ffmpeg=downloading` (banner, Start waits). The mock persists
  settings in `localStorage` (`media-forge.mock.settings`, `theme` included), probes in 300-900 ms with generated
  thumbnails, runs jobs with plausible stages / speed / ETA, fails one job in ten, mimics size limits, implements
  normalisation exactly as BRIDGE_API 3.4, re-queues `error` (read) and `done` items in `start([id])` with a new
  numbered output name, starts an item at once in `start_now(id)` (a queued one leaves the queue; the parallel
  limit is ignored), opens `open_url` links with `window.open`, and cycles the update status on demand (manual
  check finds 0.2.0; Download; Restart).
- Mock-only hook: `_simulate_drop(files)` (underscored, not part of the contract): browser drops become items by name
  through the `items_added` event, like the shell's drop listener.
- Theme persistence: the shell keeps `theme` in `settings.json`; the page mirrors the confirmed value in
  `localStorage['media-forge.theme']` for the first paint of the next launch (the shell's fixed HTTP port range
  keeps the page origin, and so the key, stable between launches). The shell's value wins once `ready()` returns.
- Bridge expectations the page relies on (BRIDGE_API): `window.mediaForge.dispatch(name, payload)` is the only hook;
  `item` events may arrive for ids the page has not seen (they create the card); `start(ids)` returns the queued ids.
- The page makes no network request: fonts (the kit's `fonts/`) and icons are local, thumbnails are data URLs.
- The kit copy is updated only by syncing the master (`vendor-sync.ps1 -Lib liquid_ui -Project media-forge`); a fix
  made in the copy is ported to the master the same day.
- Screenshots of a hidden browser pane: rAF and WAAPI pause there, so springs and entrances hold their first frame;
  finish finite animations first (`document.getAnimations().forEach(a => a.finish())`, skipping infinite ones).
- Every lens owns one `<filter>`; panels, toasts and controls destroy their lenses when removed.
- Sound when done (the page's job per BRIDGE_API 3.5; the shell plays nothing): `js/sound.js` synthesises a short two-note chime with Web Audio on `batch_done` with at least one
  finished file, at most once per second; no audio file is shipped. `open_when_done` is the shell's job; the page
  only shows the switch.

## 8. Copy rules

- Neutral and factual: say what a control does or what happened, no filler,
  no exclamation marks. English (US), sentence case: "Choose files", "Start", "Clear done", "Show in folder", "Open",
  "Retry", "Apply to all video", "Remove", "Start now", "Cancel", "Check for updates", "Restart to update", "Try again".
  The theme row's copy is the kit's: "Theme", "Background colour and accent of the window", "Dusk", "Nova", "Frost",
  "Bloom"; the group is "Appearance".
- The bridge's kind `image` reads as "Photo" (chip), "photos" (Apply to all photos, counts). Format and codec labels
  are shown exactly as `Options` gives them; summaries shorten "MP3 · audio only" to "MP3".
- Plurals through `plural()` / `nFiles()` / `nKind()`; sizes in B / KB / MB / GB, percentages with a no-break space
  ("19 %"), ETA "1 min 12 s left", durations "1:12".
- Strings from the shell (errors, toasts, UpdateStatus.message) are shown as received.
