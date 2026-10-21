# Build, run and troubleshoot

Toolchain and versions follow the pywebview family baseline of `D:\Projects\_workspace\RECIPES.md`, section 4;
the versions below are the ones verified on 2026-10-21.

## 1. Toolchain

| Part | Version | Note |
|---|---|---|
| Python | 3.14.5 (`py -3.14`) | python.org install manager |
| pywebview | 6.2.1 (`>=6.2.1,<7`) | `gui="edgechromium"`, `private_mode=False`, `storage_path=<data>\webview` |
| pythonnet | 3.2.1 (`>=3.2.1`) | WebView2 / WinForms backend of pywebview (clr_loader 0.3.1) |
| NumPy | 2.5.4 | frames of the model upscale |
| Pillow | 13.0.0 (`>=13`) | images; AVIF built into the wheel; also draws the icon |
| pillow-heif | 1.8.1 | HEIC / HEIF read and write |
| opencv-contrib-python-headless | 5.0.0.93 (`>=4.10`) | `dnn_superres` (FSRCNN, EDSR) |
| PyInstaller | 6.22.3 (`>=6.22`), pyinstaller-hooks-contrib 2026.9 | one-file, windowed, no UPX |
| ffmpeg / ffprobe | 9.0.2 full build | not bundled: `media-forge_data\bin`, then `PATH`, else downloaded once by the app |

Runtime requirement on the target machine: Microsoft Edge WebView2 Runtime (preinstalled on Windows 11 and current
Windows 10). The exe needs no Python.

Requirements files: `requirements.txt` (runtime: pywebview, pythonnet, numpy, Pillow, pillow-heif, OpenCV contrib
headless), `requirements-dev.txt` (PyInstaller). `pyproject.toml` carries the project metadata (`src` layout,
`setuptools`, version read from `media_forge.__version__`); neither the run nor the build installs the package, both
put `src` on `PYTHONPATH`.

## 2. Development run

```powershell
powershell -ExecutionPolicy Bypass -File build\build.ps1 -SkipBuild    # creates .venv and installs both requirements files
$env:MEDIA_FORGE_HOME = "$env:TEMP\media_forge_scratch"
scripts\run_dev.bat [--fake] [--ui-dir PATH]
```

`scripts\run_dev.bat` sets `PYTHONPATH=src` and runs `.venv\Scripts\python -m media_forge.shell.main --debug %*`
(`py -3.14` when `.venv` does not exist). `MEDIA_FORGE_HOME` is left to the caller: without it the data folder is
`media-forge_data\` in the project root (ignored by git). Flags:

- `--debug`: WebView2 dev tools (a DevTools window opens with the app), DEBUG logging on the console.
- `--fake`: the stand-in engine of `tests\shell_fake_engine.py` instead of `media_forge.core.engine.Engine`
  (simulated probing and progress, nothing read or written). Source tree only; the exe does not contain it.
- `--ui-dir PATH`: serve the UI from another folder (a folder with an `index.html`).

The window loads `ui\index.html` through pywebview's local HTTP server on `http://127.0.0.1:<port>/`, the first free
port from 42101 to 42140, so the page origin and its `localStorage` stay the same between launches. Logs:
`<data folder>\logs\app.log` (rotating, 1 MB x 3; core, shell, UI `log()` calls and pywebview warnings). A
development run also checks GitHub for a newer release (the updater's normal behaviour); applying an update works
only in the packaged exe.

UI alone in a browser with the JS mock: `python -m http.server 5173 --directory src\media_forge\ui`, then
`http://localhost:5173/?mock=1`.

Checking the real window from a script (no clicks): a launcher that sets
`webview.settings["REMOTE_DEBUGGING_PORT"] = <port>` before importing `media_forge.shell.main` and then calls
`main([...])`, driven over CDP with Node 24 (`D:\Projects\_workspace\NOTES.md`, "Driving a pywebview window"). With
`--debug` the DevTools window is a second `page` target: pick the target whose URL starts with
`http://127.0.0.1:`.

## 3. Tests

```powershell
$env:MEDIA_FORGE_HOME = "$env:TEMP\media_forge_scratch"
$env:PYTHONPATH = "src"
.venv\Scripts\python -m unittest discover -s tests -t .                          # everything
.venv\Scripts\python -m unittest discover -s tests -t . -p "test_shell_*.py"     # shell only
```

Shell tests never open a window:

| File | Covers |
|---|---|
| `test_shell_events.py` | `EventPump`: nothing before `ready()`, `item` coalesced per id keeping its place, other events in order, `item_removed` drops a pending `item`, `items_added` goes before pending updates of its items, threaded delivery, JS escaping (`js_literal`, U+2028 / U+2029) |
| `test_shell_bridge.py` | public names = exactly BRIDGE_API.md 2 (and the parameter names pywebview sees), delegation to the engine, argument checks, error sentences, settings refusals (3.5 sentences, settings re-sent), dialogs and their filters, minimize / close, focus events, shutdown, the drop listener, self-update wiring (check, download, apply closes the window, auto download, interval change) |
| `test_shell_drop.py` | `pywebviewFullPath` extraction, one-level folder expansion to media files, the extension table against `CORE.md` 2 and the core's `detect_kind` |
| `test_shell_selfupdate.py` | the `core\selfupdate.py` adapter: repository, asset, names, environment variable, exe path when frozen, scheduler, `--apply-update` routed before the mutex |
| `test_shell_winutil.py` | size ladder against work areas, centring, the DWM border `COLORREF`, the hook and DWM helpers without a window, no resize code left |
| `test_shell_main.py` | arguments, UI folder (source and frozen), port range, data paths, single instance |

`tests\shell_fake_engine.py` is the stand-in engine used by these tests and by `--fake`.

## 4. Build

```powershell
powershell -ExecutionPolicy Bypass -File build\build.ps1              # venv, deps, tests, icon, exe, SHA256SUMS
powershell -ExecutionPolicy Bypass -File build\build.ps1 -SkipTests
powershell -ExecutionPolicy Bypass -File build\build.ps1 -SkipBuild   # only the venv and the dependencies
```

Steps: create `.venv` (`py -3.14`) and install both requirements files; run the whole suite with a scratch
`MEDIA_FORGE_HOME` and `PYTHONPATH=src`; `build\make_icon.py` writes `build\icon.ico` (sizes 16 to 256; uses
`src\media_forge\ui\assets\icon-1024.png` instead of the drawn tile when that file exists; `--png <file>` also
writes a 256 px preview); PyInstaller with `build\MediaForge.spec` (output `build\dist`, work files `build\work`);
copy to `release\MediaForge.exe`; write `release\SHA256SUMS`.

Output in `release\` (ignored): `MediaForge.exe`, `SHA256SUMS` (`<sha256>  MediaForge.exe`, two spaces).

`build\MediaForge.spec` details: entry `build\entry.py` (calls `media_forge.shell.main.main()`); `src` on `pathex`;
`src\media_forge\ui` bundled under `media_forge/ui` (read from `sys._MEIPASS\media_forge\ui` by
`main.resolve_ui_dir`); `src\media_forge\models\*.pb` and `*.md` under `media_forge/models` (read by
`core.paths.bundled_models_dir`); `build\icon.ico` at the archive root (window icon) and as the exe icon;
`collect_submodules("media_forge")` covers `core`, `shell` and `vendor.selfupdate`; hidden imports for the pywebview
WinForms / Edge backends, `clr_loader`, `pillow_heif`, `PIL.AvifImagePlugin`, `cv2`; OpenCV's
`opencv_videoio_ffmpeg*.dll` is left out (video never goes through cv2); Windows version resource written to
`build\work\_version_info.txt` from `__version__`; the spec stops when `src\media_forge\ui\index.html` is missing.

Verified 2026-10-21 on a scratch copy of the tree (the UI had no `index.html` yet, a placeholder page stood in):
PyInstaller finished in about 25 s; `MediaForge.exe` is 72.6 MB (85 MB before the videoio DLL was dropped; the
OpenCV contrib `cv2.pyd`, 112 MB unpacked, is most of it); started from a scratch folder the window appeared after
2.4 s with a 630 x 840 viewport, the data folder was created beside the exe, ffmpeg was found on `PATH`, AVIF and
HEIF were available, the bundled FSRCNN models were found, and closing the window ended every process.

## 5. Release

Per `D:\Projects\_workspace\CONVENTIONS.md`, "Versioning and releases": notes in `docs/releases/vX.Y.Z.md`, version
bump in `src\media_forge\__init__.py`, commit `Release vX.Y.Z`, tag `vX.Y.Z` on `dev`, GitHub Release titled
`vX.Y.Z` with exactly two assets, `MediaForge.exe` and `SHA256SUMS` from `release\` (the self-updater looks both up
by name). The first publication and every major version need the user's explicit yes; afterwards a finished minor or
patch version is released as soon as tests, build, the pre-publish checklist and the notes are in order.

Self-update end-to-end test before a release that touches the update path (`RECIPES.md` 1): build N and N+1, serve
`latest.json`, the N+1 exe and its `SHA256SUMS` with `python -m http.server 8765`, set
`MEDIA_FORGE_RELEASE_URL=http://127.0.0.1:8765/latest.json`, run N from a scratch folder, Download, Restart; N+1
must run from the same path, `MediaForge.exe.old` must be gone within about 15 s, the data folder untouched.

## 6. Window notes

- Fixed size (`ARCHITECTURE.md` 7): `create_window(resizable=False, frameless=True)`; pywebview first makes the form
  `FixedSingle` and then borderless, which keeps the client size and leaves a 614 x 801 window at 96 dpi, so the
  shell sets the real size itself after the form is shown.
- `shell\winutil.FixedSizeHook` subclasses the window procedure: `WM_WINDOWPOSCHANGING` replaces every size of
  the restored window with the locked one (a `SetWindowPos` from another program cannot resize it),
  `SC_MAXIMIZE` is swallowed, a `SIZE_MAXIMIZED` that still arrives (`ShowWindow(SW_MAXIMIZE)` from outside) is
  undone with `SC_RESTORE`, `WS_MAXIMIZEBOX` / `WS_THICKFRAME` are cleared and `WS_MINIMIZEBOX` is set (taskbar
  click minimizes). Without these, the borderless form keeps the `WS_MAXIMIZEBOX` bit and fills the work area on
  `SC_MAXIMIZE` (measured with pywebview 6.2.1).
- Size: the first of 630 x 840, 600 x 800, 570 x 760, 540 x 720 logical px (`main.SIZE_LADDER`) that fits the
  monitor work area at the window's DPI, centred. The process is system-DPI aware (pywebview calls
  `SetProcessDPIAware`), so the window gets no `WM_DPICHANGED`.
- DWM: rounded corners (`DWMWA_WINDOW_CORNER_PREFERENCE`) and the border colour `DWMWA_BORDER_COLOR` = the page
  background `#04070f`, so the light Windows 11 frame line is gone; the page covers the whole client area.
- Drag regions (`pywebview-drag-region`) move the window from JavaScript (`window.move`, no size change); a
  double-click there is a plain DOM event and never maximizes.
- Checked with a CDP launcher and a Win32 probe (`GetWindowRect`): start 630 x 840, `SetWindowPos` to 1000 x 600
  stays 630 x 840, `SC_MAXIMIZE` / `ShowWindow(SW_MAXIMIZE)` / double-click on the title bar change nothing, six
  minimize / restore cycles keep 630 x 840, the top screen row of the window is `#04070f`.

## 7. Troubleshooting

Symptom / cause / fix entries specific to this project; cross-project ones go to
`D:\Projects\_workspace\NOTES.md`.

### The frozen exe shows two processes
- Symptom: Task Manager lists two `MediaForge.exe`; ending the first one leaves the window running.
- Cause: the PyInstaller one-file bootloader is the parent process, the app runs in the child.
- Fix: none needed; closing the window ends both. When scripting a close, send it to the process that has the
  window title.

### A dropped file is not added
- Symptom: dropping files on the window does nothing.
- Cause: pywebview attaches `pywebviewFullPath` only when a Python-side DOM `drop` listener exists; the shell adds
  it when the page has loaded (`window.events.loaded`). A drop before that, or a synthetic `DragEvent`, carries no
  path (`NOTES.md`, "Dropped files have no path in the page").
- Fix: check `app.log` for "drag and drop is not available"; real OS drops only.
