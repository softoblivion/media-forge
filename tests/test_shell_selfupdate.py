"""Media Forge configuration of the vendored self-updater (ARCHITECTURE.md 5.7)."""

from __future__ import annotations

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
        self.assertEqual(vendored.__version__, "0.4.0")

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


if __name__ == "__main__":
    unittest.main()
