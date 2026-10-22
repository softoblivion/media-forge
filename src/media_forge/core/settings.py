"""Global settings and the per-kind default item settings (BRIDGE_API.md 3.5), persisted in `settings.json`.

A refused patch raises `UserError` and changes nothing. A broken file is kept as `settings.json.bad` and replaced
by the defaults.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Mapping

from . import catalog
from .errors import MSG_OUTPUT_DIR_MISSING, UserError
from .paths import AppPaths, atomic_write_json, replace_file

log = logging.getLogger("media_forge.core.settings")

SCHEMA_VERSION = 1
OUTPUT_MODES = ("beside", "folder")
THEMES = ("dusk", "nova", "frost", "bloom")  # the kit's four themes (BRIDGE_API.md 3.5)
SUFFIX_MAX = 24
_SUFFIX_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

MSG_SUFFIX_NEEDED = "A suffix is needed when files are written next to the source."
MSG_SUFFIX_CHARS = "The suffix contains characters that are not allowed in file names."
MSG_SUFFIX_LONG = "The suffix can be at most 24 characters long."
MSG_SETTINGS_TYPE = "The settings could not be read."

GLOBAL_DEFAULTS: dict[str, Any] = {
    "output_mode": "beside",
    "output_dir": "",
    "suffix": "_forge",
    "parallel_jobs": 1,
    "open_when_done": False,
    "sound_when_done": True,
    "auto_download_updates": True,
    "theme": "nova",
}


def default_settings() -> dict[str, Any]:
    data = copy.deepcopy(GLOBAL_DEFAULTS)
    data["defaults"] = {kind: catalog.defaults(kind) for kind in catalog.KINDS}
    return data


def folder_usable(path: str) -> bool:
    """The folder exists and a file can be created in it."""
    if not path:
        return False
    folder = Path(path)
    if not folder.is_dir():
        return False
    return os.access(folder, os.W_OK)


def _check_suffix(value: Any) -> str:
    if not isinstance(value, str):
        raise UserError(MSG_SUFFIX_CHARS)
    if len(value) > SUFFIX_MAX:
        raise UserError(MSG_SUFFIX_LONG)
    if _SUFFIX_BAD.search(value) or value.endswith((".", " ")):
        raise UserError(MSG_SUFFIX_CHARS)
    return value


def validate(current: Mapping[str, Any], patch: Mapping[str, Any],
             caps: Mapping[str, Any] | None = None, check_folder: bool = True) -> dict[str, Any]:
    """Return the new complete settings or raise UserError. Unknown keys of the patch are ignored."""
    if not isinstance(patch, Mapping):
        raise UserError(MSG_SETTINGS_TYPE)
    new = copy.deepcopy(dict(current))
    for key in GLOBAL_DEFAULTS:
        if key not in patch:
            continue
        value = patch[key]
        if key == "output_mode":
            new[key] = value if value in OUTPUT_MODES else GLOBAL_DEFAULTS[key]
        elif key == "output_dir":
            new[key] = str(value or "").strip()
        elif key == "suffix":
            new[key] = _check_suffix(value)
        elif key == "parallel_jobs":
            new[key] = catalog._int_clamp(value, 1, 3, GLOBAL_DEFAULTS[key])
        elif key == "theme":
            new[key] = value if value in THEMES else GLOBAL_DEFAULTS[key]
        else:  # booleans
            new[key] = value if isinstance(value, bool) else bool(current.get(key, GLOBAL_DEFAULTS[key]))
    if new["output_mode"] == "folder" and check_folder and ("output_mode" in patch or "output_dir" in patch):
        if not folder_usable(new["output_dir"]):
            raise UserError(MSG_OUTPUT_DIR_MISSING)
    if new["output_mode"] == "beside" and new["suffix"] == "":
        raise UserError(MSG_SUFFIX_NEEDED)
    defaults_patch = patch.get("defaults")
    if isinstance(defaults_patch, Mapping):
        for kind in catalog.KINDS:
            if isinstance(defaults_patch.get(kind), Mapping):
                new["defaults"][kind] = catalog.merge(kind, new["defaults"].get(kind), defaults_patch[kind], caps)
    return new


def sanitize(data: Any) -> dict[str, Any]:
    """Settings read from disk: every key repaired individually (a bad value never discards the whole file)."""
    result = default_settings()
    if not isinstance(data, Mapping):
        return result
    for key in GLOBAL_DEFAULTS:
        if key not in data:
            continue
        try:
            result = validate(result, {key: data[key]}, check_folder=False)
        except UserError:
            pass
    if result["output_mode"] == "beside" and result["suffix"] == "":
        result["suffix"] = GLOBAL_DEFAULTS["suffix"]
    stored = data.get("defaults")
    if isinstance(stored, Mapping):
        for kind in catalog.KINDS:
            if isinstance(stored.get(kind), Mapping):
                result["defaults"][kind] = catalog.normalize(kind, stored[kind])
    return result


class Settings:
    """Thread-safe holder of the settings; `persist=False` keeps them in memory only (CLI)."""

    def __init__(self, paths: AppPaths, persist: bool = True) -> None:
        self.paths = paths
        self.persist = persist
        self._lock = threading.RLock()
        self._data = default_settings()

    def load(self) -> dict[str, Any]:
        path = self.paths.settings_file
        with self._lock:
            if not path.exists():
                self._data = default_settings()
                return self.get()
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("settings.json is not an object")
                self._data = sanitize(raw)
            except (OSError, ValueError) as exc:
                log.warning("settings.json is unreadable (%s); keeping it as settings.json.bad", exc)
                try:
                    replace_file(path, path.with_name(path.name + ".bad"))
                except OSError:
                    pass
                self._data = default_settings()
                self.save()
            return self.get()

    def save(self) -> None:
        if not self.persist:
            return
        with self._lock:
            data = copy.deepcopy(self._data)
        data["version"] = SCHEMA_VERSION
        try:
            atomic_write_json(self.paths.settings_file, data)
        except OSError:
            log.exception("settings.json could not be written")

    def get(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    def value(self, key: str) -> Any:
        with self._lock:
            return copy.deepcopy(self._data.get(key))

    def item_defaults(self, kind: str) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data["defaults"][kind])

    def patch(self, patch: Mapping[str, Any], caps: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            self._data = validate(self._data, patch, caps)
            result = self.get()
        self.save()
        return result

    def renormalize_defaults(self, caps: Mapping[str, Any] | None) -> bool:
        """Apply capability-dependent rules (NVENC, HEIF) to the stored defaults; True when something changed."""
        changed = False
        with self._lock:
            for kind in catalog.KINDS:
                fixed = catalog.normalize(kind, self._data["defaults"][kind], caps)
                if fixed != self._data["defaults"][kind]:
                    self._data["defaults"][kind] = fixed
                    changed = True
        if changed:
            self.save()
        return changed
