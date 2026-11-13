"""Location of the runtime data folder and atomic file writes.

Nothing outside this module builds a path inside the data folder (ARCHITECTURE.md 4, CORE.md section 1).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

APP_DIR_NAME = "media-forge_data"
ENV_HOME = "MEDIA_FORGE_HOME"

log = logging.getLogger("media_forge.core.paths")


def app_dir() -> Path:
    """Folder of the exe when frozen, else the project root (the parent of `src`)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def bundled_models_dir() -> Path:
    """Folder of the bundled model files: `media_forge/models` next to this package.

    PyInstaller keeps the package layout inside its extraction folder, so the same relative path works frozen
    (the spec file adds `src/media_forge/models/*.pb` as data under `media_forge/models`).
    """
    return Path(__file__).resolve().parent.parent / "models"


class AppPaths:
    """Resolved paths of the data folder. The environment is read when the object is created."""

    def __init__(self, home: Path | str | None = None) -> None:
        if home is None:
            env = os.environ.get(ENV_HOME, "").strip()
            home = Path(env) if env else app_dir() / APP_DIR_NAME
        self.home: Path = Path(home)
        self.bin_dir: Path = self.home / "bin"
        self.models_dir: Path = self.home / "models"
        self.settings_file: Path = self.home / "settings.json"
        self.cache_dir: Path = self.home / "cache"
        self.thumbs_dir: Path = self.cache_dir / "thumbs"
        self.jobs_dir: Path = self.cache_dir / "jobs"
        self.log_dir: Path = self.home / "logs"
        self.job_logs_dir: Path = self.log_dir / "jobs"
        self.app_log: Path = self.log_dir / "app.log"
        self.webview_dir: Path = self.home / "webview"
        self.updates_dir: Path = self.home / "updates"
        self.versions_file: Path = self.bin_dir / "versions.json"
        self.models_file: Path = self.models_dir / "models.json"

    def ensure(self) -> None:
        """Create the data folder and its sub-folders."""
        for folder in (self.home, self.bin_dir, self.models_dir, self.cache_dir, self.thumbs_dir, self.jobs_dir,
                       self.log_dir, self.job_logs_dir, self.updates_dir):
            folder.mkdir(parents=True, exist_ok=True)

    def clear_cache(self) -> None:
        """Empty `cache/thumbs` and `cache/jobs` (called on start; leftovers of a crashed run)."""
        for folder in (self.thumbs_dir, self.jobs_dir):
            if folder.is_dir():
                for child in folder.iterdir():
                    remove_path(child)

    def binary(self, name: str) -> Path:
        return self.bin_dir / name

    def model_file(self, filename: str) -> Path:
        return self.models_dir / filename

    def job_cache(self, job_id: str) -> Path:
        folder = self.jobs_dir / job_id
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def job_log(self, job_id: str) -> Path:
        self.job_logs_dir.mkdir(parents=True, exist_ok=True)
        return self.job_logs_dir / f"{job_id}.log"


def remove_path(path: Path) -> None:
    """Delete a file or a folder tree; missing paths and locked files are ignored."""
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


SHARING_ERRORS = (32, 5)  # ERROR_SHARING_VIOLATION, ERROR_ACCESS_DENIED (a delete-pending or read-only file too)


def remove_output(path: Path, timeout: float = 3.0, step: float = 0.15) -> bool:
    """Delete the partial output of a failed job. A file another process has just opened (a virus scanner, the
    search indexer, a thumbnailer) refuses deletion for a moment, so a sharing or access error is retried for up to
    `timeout` seconds; True when the file is gone, else the file is left and a warning names it."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            Path(path).unlink()
            return True
        except FileNotFoundError:
            return True
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in SHARING_ERRORS or time.monotonic() >= deadline:
                log.warning("the partial output %s could not be removed: %s", path, exc)
                return False
        except OSError as exc:
            log.warning("the partial output %s could not be removed: %s", path, exc)
            return False
        time.sleep(step)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write `data` to `<name>.part` next to `path`, then replace `path` with it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    try:
        with open(part, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        replace_file(part, path)
    except BaseException:
        try:
            part.unlink()
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, text: str) -> None:
    """Write UTF-8 text without BOM; line endings are kept exactly as given."""
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: Path, obj: object) -> None:
    """Write `obj` as indented UTF-8 JSON."""
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def copy_file_times(source: Path, target: Path) -> None:
    """Give `target` the modified and accessed times of `source`, and on Windows its creation time as well.
    Raises OSError when a time cannot be set."""
    st = os.stat(source)
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))
    birth = getattr(st, "st_birthtime_ns", None)
    if sys.platform == "win32" and birth:
        _set_creation_time(Path(target), int(birth))


_EPOCH_AS_FILETIME = 116444736000000000  # 1970-01-01 in 100 ns ticks since 1601-01-01


def _set_creation_time(path: Path, ns: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.SetFileTime.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                                     ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME))
    kernel32.SetFileTime.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    ticks = max(0, ns // 100 + _EPOCH_AS_FILETIME)
    created = wintypes.FILETIME(ticks & 0xFFFFFFFF, ticks >> 32)
    # FILE_WRITE_ATTRIBUTES, share read / write / delete, OPEN_EXISTING
    handle = kernel32.CreateFileW(str(path), 0x100, 0x7, None, 3, 0, None)
    if handle is None or handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not kernel32.SetFileTime(handle, ctypes.byref(created), None, None):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32.CloseHandle(handle)


def replace_file(source: Path, target: Path) -> None:
    """os.replace with a short retry: Windows refuses while another process (scanner, indexer) holds the target."""
    for attempt in range(6):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.05 * (attempt + 1))
