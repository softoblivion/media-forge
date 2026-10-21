# -*- mode: python ; coding: utf-8 -*-
# One-file, windowed PyInstaller build of Media Forge. Run through build/build.ps1 (see docs/BUILD.md).
import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent
SRC = ROOT / "src"
PKG = SRC / "media_forge"
UI_DIR = PKG / "ui"
MODELS_DIR = PKG / "models"
ICON = ROOT / "build" / "icon.ico"
WORK = ROOT / "build" / "work"

if not (UI_DIR / "index.html").is_file():
    raise SystemExit(f"index.html not found in {UI_DIR}: the UI is required for the build")

# Version is read from the one place that owns it, without importing the package.
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', (PKG / "__init__.py").read_text("utf-8")).group(1)

# --- Windows version resource ------------------------------------------------------------------------------------
_parts = (tuple(int(p) for p in VERSION.split(".")) + (0, 0, 0, 0))[:4]
WORK.mkdir(parents=True, exist_ok=True)
_version_file = WORK / "_version_info.txt"
_version_file.write_text(
    f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={_parts}, prodvers={_parts}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('FileDescription', 'Media Forge'),
      StringStruct('FileVersion', '{VERSION}'),
      StringStruct('InternalName', 'MediaForge'),
      StringStruct('OriginalFilename', 'MediaForge.exe'),
      StringStruct('ProductName', 'Media Forge'),
      StringStruct('ProductVersion', '{VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""",
    encoding="utf-8",
)

# --- bundle contents ---------------------------------------------------------------------------------------------
# main.resolve_ui_dir reads sys._MEIPASS / "media_forge" / "ui"; core.paths.bundled_models_dir reads
# media_forge/models next to the package; main.resolve_icon reads icon.ico at the archive root.
datas = [(str(UI_DIR), "media_forge/ui")]
for pattern in ("*.pb", "*.md"):
    for model_file in sorted(MODELS_DIR.glob(pattern)):
        datas.append((str(model_file), "media_forge/models"))
if ICON.is_file():
    datas.append((str(ICON), "."))
# pywebview, pythonnet / clr_loader and cv2 bring their own PyInstaller hooks (pywebview's package hook and
# pyinstaller-hooks-contrib); pillow_heif ships no data files of its own that need collecting.

# Every package module (core, shell and the vendored selfupdate); main.py imports several of them late.
hiddenimports = collect_submodules("media_forge") + [
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
    "clr_loader",
    "pythonnet",
    "pillow_heif",
    "PIL.AvifImagePlugin",
    "cv2",
    "numpy",
]

excludes = [
    "tkinter", "pydoc", "doctest", "idlelib", "lib2to3",
    "matplotlib", "scipy", "pandas", "IPython", "pytest", "setuptools", "pip", "wheel",
    "PyQt5", "PyQt6", "PySide2", "PySide6", "qtpy", "gi", "cefpython3",
]

a = Analysis(
    [str(ROOT / "build" / "entry.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
# OpenCV's own ffmpeg video I/O plugin (about 31 MB) is never used: video goes through the ffmpeg executable and
# cv2 only runs dnn_superres on NumPy frames (core/upscale.py). Leave it out of the exe.
a.binaries = [entry for entry in a.binaries if "opencv_videoio_ffmpeg" not in entry[0].lower()]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="MediaForge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-packed exes trigger antivirus false positives
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon=str(ICON) if ICON.is_file() else None,
    version=str(_version_file),
)
