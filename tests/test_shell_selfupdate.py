"""Media Forge configuration of the vendored self-updater (ARCHITECTURE.md 5.7)."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from media_forge.core import selfupdate  # noqa: E402
from media_forge.vendor import selfupdate as vendored  # noqa: E402


class Paths:
    def __init__(self, root: Path, exe_path=None):
        self.home = root
        self.updates_dir = root / "updates"
        if exe_path is not None:
            self.exe_path = exe_path


class AdapterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mf_shell_update_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_constants(self):
        self.assertEqual(selfupdate.REPO, "softoblivion/media-forge")
        self.assertEqual(selfupdate.ASSET_NAME, "MediaForge.exe")
        self.assertEqual(selfupdate.APP_NAME, "Media Forge")
        self.assertEqual(selfupdate.RELEASE_URL_ENV, "MEDIA_FORGE_RELEASE_URL")
        self.assertEqual(selfupdate.LOGGER_NAME, "media_forge.selfupdate")
        self.assertEqual(selfupdate.APPLY_FLAG, "--apply-update")
        for gone in ("INTERVAL_KEYS", "CHECK_INTERVALS", "DEFAULT_CHECK_INTERVAL"):
            self.assertFalse(hasattr(selfupdate, gone), gone)

    def test_reexports_are_the_vendored_objects(self):
        self.assertIs(selfupdate.SelfUpdater, vendored.SelfUpdater)
        self.assertIs(selfupdate.run_apply, vendored.run_apply)
        self.assertEqual(vendored.__version__, "0.5.0")
        for gone in ("is_major_upgrade", "major_of"):
            self.assertFalse(hasattr(vendored, gone), gone)
        self.assertNotIn("major", vendored.UpdateStatus.__annotations__)

    def test_config_from_source(self):
        statuses = []
        config = selfupdate.build_config(Paths(self.tmp), "0.1.0", statuses.append)
        self.assertEqual(config.repo, "softoblivion/media-forge")
        self.assertEqual(config.asset_name, "MediaForge.exe")
        self.assertEqual(config.app_name, "Media Forge")
        self.assertEqual(config.version, "0.1.0")
        self.assertEqual(config.updates_dir, str(self.tmp / "updates"))
        self.assertIsNone(config.exe_path)  # not frozen: can_apply stays false
        self.assertEqual(config.release_url_env, "MEDIA_FORGE_RELEASE_URL")
        self.assertEqual(config.logger_name, "media_forge.selfupdate")
        self.assertEqual(config.sums_name, "SHA256SUMS")
        self.assertEqual(config.download_name("1.2.3"), "MediaForge-1.2.3.exe")

    def test_exe_path_when_frozen(self):
        exe = self.tmp / "MediaForge.exe"
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(sys, "executable", str(exe)):
            config = selfupdate.build_config(Paths(self.tmp), "0.1.0", lambda s: None)
        self.assertEqual(config.exe_path, str(exe.resolve()))

    def test_explicit_exe_path_wins(self):
        config = selfupdate.build_config(Paths(self.tmp, exe_path=self.tmp / "X.exe"), "0.1.0", lambda s: None)
        self.assertEqual(config.exe_path, str(self.tmp / "X.exe"))

    def test_updater_status_and_scheduler(self):
        updater = selfupdate.build_updater(Paths(self.tmp), "0.1.0", lambda s: None)
        status = updater.status()
        self.assertEqual(status["stage"], "idle")
        self.assertEqual(status["current"], "0.1.0")
        self.assertFalse(status["can_apply"])
        for key in ("stage", "current", "latest", "notes", "size", "published_at", "progress", "message",
                    "checked_at", "can_apply"):
            self.assertIn(key, status)
        scheduler = selfupdate.build_scheduler(updater)
        self.assertIsNone(scheduler.next_check_at)

    def test_scheduler_period_is_five_minutes(self):
        updater = selfupdate.build_updater(Paths(self.tmp), "0.1.0", lambda s: None)
        scheduler = selfupdate.build_scheduler(updater)
        self.addCleanup(scheduler.stop)
        self.assertEqual(vendored.CHECK_PERIOD, 300)
        before = time.time()
        scheduler.start()
        ahead = scheduler.next_check_at - before
        self.assertGreater(ahead, vendored.CHECK_PERIOD - vendored.CHECK_JITTER - 1)
        self.assertLess(ahead, vendored.CHECK_PERIOD + vendored.CHECK_JITTER + 1)

    def test_release_url_override(self):
        config = selfupdate.build_config(Paths(self.tmp), "0.1.0", lambda s: None)
        with mock.patch.dict("os.environ", {"MEDIA_FORGE_RELEASE_URL": "http://127.0.0.1:8765/latest.json"}):
            self.assertEqual(config.release_url_override(), "http://127.0.0.1:8765/latest.json")

    def test_main_routes_apply_flag_first(self):
        from media_forge.shell import main

        with mock.patch.object(main, "run_apply", return_value=0) as run_apply, \
                mock.patch("media_forge.shell.winutil.acquire_single_instance") as mutex:
            self.assertEqual(main.main(["--apply-update", "123", "C:\\x\\MediaForge.exe"]), 0)
        run_apply.assert_called_once_with(["123", "C:\\x\\MediaForge.exe"])
        mutex.assert_not_called()


class ReleaseNet:
    """Canned release of the GitHub API format: get_text answers the release JSON and SHA256SUMS, download writes
    the payload. No network."""

    def __init__(self, tag: str, payload: bytes):
        self.payload = payload
        base = f"https://github.com/{selfupdate.REPO}/releases/download/{tag}"
        self.texts = {
            f"https://api.github.com/repos/{selfupdate.REPO}/releases/latest": json.dumps({
                "tag_name": tag, "body": "## Changed\n- things", "published_at": "2026-11-13T10:00:00Z",
                "assets": [{"name": name, "size": len(payload) if name == selfupdate.ASSET_NAME else 100,
                            "browser_download_url": f"{base}/{name}"}
                           for name in (selfupdate.ASSET_NAME, "SHA256SUMS")]}),
            f"{base}/SHA256SUMS": f"{hashlib.sha256(payload).hexdigest()}  {selfupdate.ASSET_NAME}\n",
        }
        self.downloads: list[str] = []

    def get_text(self, url):
        if url not in self.texts:
            raise OSError(f"{url}: HTTP 404")
        return self.texts[url]

    def download(self, url, dest, on_progress=None):
        self.downloads.append(url)
        Path(dest).write_bytes(self.payload)
        if on_progress:
            on_progress(len(self.payload), len(self.payload))
        return hashlib.sha256(self.payload).hexdigest()


class NewerMajorTest(unittest.TestCase):
    """A release with a greater major version is an ordinary update: the bridge's automatic download fetches it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mf_shell_major_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_newer_major_with_automatic_download_is_downloaded_and_becomes_ready(self):
        from media_forge.shell.bridge import Bridge, EventPump
        from tests.shell_fake_engine import FakeEngine

        pump = EventPump(threaded=False)
        self.addCleanup(pump.stop)
        engine = FakeEngine(pump.emit, "2.4.0", probe_delay=None)
        self.assertIs(engine.get_settings()["auto_download_updates"], True)  # the default
        bridge = Bridge(engine, pump, "2.4.0")
        exe = self.tmp / "app" / "MediaForge.exe"
        exe.parent.mkdir()
        exe.write_bytes(b"OLD-EXE")
        net = ReleaseNet("v3.0.0", b"MZ" + b"new-exe" * 1000)
        config = dataclasses.replace(
            selfupdate.build_config(Paths(self.tmp / "home", exe_path=exe), "2.4.0", bridge._on_update_status,
                                    bridge._on_update_toast), net=net)
        spawned = []
        updater = selfupdate.SelfUpdater(config, spawn=lambda *args: spawned.append(args))
        bridge._attach_updater(updater)

        found = bridge.check_updates()
        deadline = time.monotonic() + 5
        while updater.status()["stage"] != "ready" and time.monotonic() < deadline:
            time.sleep(0.02)
        status = updater.status()
        self.assertIn(found["stage"], ("checking", "available", "downloading", "ready"))
        self.assertEqual((status["stage"], status["latest"], status["message"], status["can_apply"]),
                         ("ready", "3.0.0", "Version 3.0.0 is ready to install", True))
        self.assertEqual(net.downloads, [f"https://github.com/{selfupdate.REPO}/releases/download/v3.0.0/"
                                         f"{selfupdate.ASSET_NAME}"])
        stages = [payload["stage"] for name, payload in pump.pending() if name == "update"]
        self.assertIn("available", stages)
        self.assertEqual(stages[-1], "ready")
        self.assertTrue(updater.apply(launch=False))
        self.assertEqual(Path(spawned[0][0]).name, "MediaForge-3.0.0.exe")


if __name__ == "__main__":
    unittest.main()
