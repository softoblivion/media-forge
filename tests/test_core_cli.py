"""cli: --help and an end-to-end conversion through the headless harness."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

from media_forge.core import procs

from tests.core_support import ScratchCase, media, needs_ffmpeg

SRC = str(Path(__file__).resolve().parents[1] / "src")


def run_cli(*args: str, home: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=SRC, MEDIA_FORGE_HOME=str(home), PYTHONUTF8="1")
    return subprocess.run([sys.executable, "-m", "media_forge.core.cli", *args], capture_output=True, text=True,
                          env=env, timeout=300, creationflags=procs.CREATE_NO_WINDOW, stdin=subprocess.DEVNULL)


class CliTest(ScratchCase):
    def test_help(self):
        res = run_cli("--help", home=self.out / "home")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("--set", res.stdout)

    @needs_ffmpeg
    def test_convert(self):
        res = run_cli(str(media("clip.mp4")), "--set", "format=webm", "--set", "quality=40", "--out", str(self.out),
                      home=self.out / "home")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("libvpx-vp9", res.stdout)  # the plan
        self.assertIn("done ->", res.stdout)
        self.assertTrue((self.out / "clip_forge.webm").is_file())
        self.assertFalse((self.out / "home" / "settings.json").exists())  # the CLI never persists settings

    @needs_ffmpeg
    def test_error_exit_code(self):
        res = run_cli(str(media("notmedia.mp4")), home=self.out / "home")
        self.assertEqual(res.returncode, 1)
        self.assertIn("Not a media file.", res.stdout)


if __name__ == "__main__":
    unittest.main()
