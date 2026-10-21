"""Media Forge configuration of the vendored self-updater.

This module is the one place that knows the application values (repository, asset name, display name); the updater
itself is `media_forge.vendor.selfupdate` and is never edited here. The contract is `docs/ARCHITECTURE.md`,
section 5.7, and the updater's own `SELF_UPDATE.md` (payload `UpdateStatus` = `docs/BRIDGE_API.md` 3.8).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

from ..vendor.selfupdate import (
    APPLY_FLAG,
    SelfUpdater,
    UpdateConfig,
    UpdateScheduler,
    run_apply,
)

REPO = "softoblivion/media-forge"
ASSET_NAME = "MediaForge.exe"
APP_NAME = "Media Forge"
RELEASE_URL_ENV = "MEDIA_FORGE_RELEASE_URL"
LOGGER_NAME = "media_forge.selfupdate"

__all__ = [
    "APPLY_FLAG",
    "APP_NAME",
    "ASSET_NAME",
    "LOGGER_NAME",
    "RELEASE_URL_ENV",
    "REPO",
    "SelfUpdater",
    "UpdateConfig",
    "UpdateScheduler",
    "build_config",
    "build_scheduler",
    "build_updater",
    "run_apply",
    "running_exe",
]


def running_exe() -> str | None:
    """The packaged exe that an update replaces; None when running from source (then `can_apply` stays false)."""
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve())
    return None


def build_config(
    paths: Any,
    version: str,
    on_status: Callable[[dict], None],
    on_toast: Callable[[str, str], None] | None = None,
) -> UpdateConfig:
    """The updater configuration. `paths` needs `updates_dir` (folder in the data folder, `core.paths.AppPaths`); an
    `exe_path` attribute is used when present, else `running_exe()`."""
    exe_path = getattr(paths, "exe_path", None) or running_exe()
    return UpdateConfig(
        repo=REPO,
        asset_name=ASSET_NAME,
        app_name=APP_NAME,
        version=version,
        updates_dir=str(paths.updates_dir),
        exe_path=str(exe_path) if exe_path else None,
        on_status=on_status,
        on_toast=on_toast,
        release_url_env=RELEASE_URL_ENV,
        logger_name=LOGGER_NAME,
    )


def build_updater(
    paths: Any,
    version: str,
    on_status: Callable[[dict], None],
    on_toast: Callable[[str, str], None] | None = None,
) -> SelfUpdater:
    """Create the updater for this application. No network is touched until a check or download is requested."""
    return SelfUpdater(build_config(paths, version, on_status, on_toast))


def build_scheduler(updater: SelfUpdater) -> UpdateScheduler:
    """Periodic checks at the updater library's fixed period (5 minutes, longer after failures, none offline)."""
    return UpdateScheduler(updater, logger_name=LOGGER_NAME + ".schedule")
