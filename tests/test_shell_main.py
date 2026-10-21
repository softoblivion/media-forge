"""Entry-point helpers: arguments, UI folder, port range, data paths, single instance, fake engine loading."""

from __future__ import annotations

import socket
import sys
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from media_forge.shell import main  # noqa: E402


class MainHelpersTest(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(main.WINDOW_TITLE, "Media Forge")
        self.assertEqual(main.MUTEX_NAME, "Local\\MediaForge.SingleInstance")
        self.assertEqual(main.BACKGROUND, "#04070f")
        self.assertEqual(main.START_SIZE, (630, 840))
        self.assertEqual(main.MIN_SIZE, (540, 720))
        self.assertEqual(main.HTTP_PORT_FIRST, 42101)

    def test_parse_args(self):
        args = main.parse_args(["--debug", "--fake", "--ui-dir", "C:\\ui"])
        self.assertTrue(args.debug and args.fake)
        self.assertEqual(args.ui_dir, "C:\\ui")
        self.assertFalse(main.parse_args([]).debug)

    def test_ui_dir(self):
        self.assertEqual(main.resolve_ui_dir(), Path(main.__file__).resolve().parents[1] / "ui")
        self.assertEqual(main.resolve_ui_dir("C:\\x\\ui"), Path("C:\\x\\ui").resolve())
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "_MEIPASS", "C:\\mei", create=True):
            self.assertEqual(main.resolve_ui_dir(), Path("C:\\mei") / "media_forge" / "ui")

    def test_port_range_skips_busy_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
            try:
                busy.bind(("127.0.0.1", main.HTTP_PORT_FIRST))
            except OSError:
                pass  # already taken by something else: still busy for the probe
            port = main.pick_http_port()
        self.assertTrue(port == 0 or main.HTTP_PORT_FIRST < port < main.HTTP_PORT_FIRST + main.HTTP_PORT_TRIES)

    def test_shell_paths_from_core_paths(self):
        class CorePaths:
            home = Path("C:\\data")
            log_dir = Path("C:\\data\\logs")
            webview_dir = Path("C:\\data\\webview")
            updates_dir = Path("C:\\data\\updates")

        paths = main.ShellPaths(CorePaths())
        self.assertEqual(paths.webview_dir, Path("C:\\data\\webview"))
        self.assertIsNone(paths.exe_path)

    def test_second_instance_brings_first_to_front(self):
        with mock.patch("media_forge.shell.winutil.acquire_single_instance", return_value=None), \
                mock.patch("media_forge.shell.winutil.bring_window_to_front") as front:
            self.assertEqual(main.main([]), 0)
        front.assert_called_once_with("Media Forge")

    def test_fake_module_loads(self):
        module = main.load_fake_module()
        engine = module.FakeEngine(lambda *_: None, "0.1.0")
        self.assertIn("items", engine.state())


if __name__ == "__main__":
    unittest.main()
