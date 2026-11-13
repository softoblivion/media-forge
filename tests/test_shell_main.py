"""Entry-point helpers: arguments, UI folder, port range, data paths, single instance, fake engine loading."""

from __future__ import annotations

import os
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
        self.assertEqual(main.SIZE_LADDER[-1], (480, 640))   # the smallest step, used at 150 % and above
        self.assertFalse(hasattr(main, "MIN_SIZE") or hasattr(main, "TOP_GRIP"))
        self.assertEqual(main.HTTP_PORT_FIRST, 42101)

    def test_parse_args(self):
        args = main.parse_args(["--debug", "--fake", "--ui-dir", "C:\\ui"])
        self.assertTrue(args.debug and args.fake)
        self.assertEqual(args.ui_dir, "C:\\ui")
        self.assertFalse(main.parse_args([]).debug)
        self.assertEqual(main.parse_args([]).files, [])

    def test_parse_args_takes_files_as_absolute_paths(self):
        # Open with / Send To / a file dropped on the exe: the shell passes the paths as plain arguments.
        args = main.parse_args(["clip one.mp4", "--debug", "C:\\media\\photo.jpg"])
        self.assertTrue(args.debug)
        self.assertEqual(args.files, [os.path.abspath("clip one.mp4"), "C:\\media\\photo.jpg"])

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

    def test_second_instance_hands_its_files_to_the_first(self):
        with mock.patch("media_forge.shell.winutil.acquire_single_instance", return_value=None), \
                mock.patch("media_forge.shell.winutil.bring_window_to_front") as front, \
                mock.patch("media_forge.shell.winutil.message_box") as box, \
                mock.patch("media_forge.shell.instance.send_files", return_value=True) as send:
            self.assertEqual(main.main(["C:\\media\\a.mp4"]), 0)
        send.assert_called_once_with(["C:\\media\\a.mp4"])
        front.assert_called_once_with("Media Forge")
        box.assert_not_called()

    def test_second_instance_without_files_sends_nothing(self):
        with mock.patch("media_forge.shell.winutil.acquire_single_instance", return_value=None), \
                mock.patch("media_forge.shell.winutil.bring_window_to_front"), \
                mock.patch("media_forge.shell.instance.send_files") as send:
            self.assertEqual(main.main(["--debug"]), 0)
        send.assert_not_called()

    def test_second_instance_says_so_when_the_first_does_not_answer(self):
        with mock.patch("media_forge.shell.winutil.acquire_single_instance", return_value=None), \
                mock.patch("media_forge.shell.winutil.bring_window_to_front"), \
                mock.patch("media_forge.shell.winutil.message_box") as box, \
                mock.patch("media_forge.shell.instance.send_files", return_value=False):
            self.assertEqual(main.main(["C:\\media\\a.mp4"]), 0)
        box.assert_called_once_with("Media Forge", main.MSG_NOT_HANDED_OVER, error=True)

    def test_fake_module_loads(self):
        module = main.load_fake_module()
        engine = module.FakeEngine(lambda *_: None, "0.1.0")
        self.assertIn("items", engine.state())


if __name__ == "__main__":
    unittest.main()
