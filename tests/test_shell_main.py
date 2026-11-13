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
        self.assertEqual(main.SIZE_LADDER[-1], (480, 640))
        self.assertEqual(main.MIN_SIZE, (480, 640))          # the smallest designed size is the minimum window
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

    def test_a_file_whose_name_starts_with_a_dash_is_a_file(self):
        args = main.parse_args(["-1.mp4", "--debug", "-h.jpg", "--fake", "--ui-dir", "C:\\ui", "--deb"])
        self.assertTrue(args.debug and args.fake)
        self.assertEqual(args.ui_dir, "C:\\ui")
        self.assertEqual(args.files, [os.path.abspath("-1.mp4"), os.path.abspath("-h.jpg"), os.path.abspath("--deb")])

    def test_closing_the_window_stops_the_hand_over_first(self):
        class Event:
            def __init__(self):
                self.handlers = []

            def __iadd__(self, handler):
                self.handlers.append(handler)
                return self

        order = []
        window = type("Window", (), {})()
        window.events = type("Events", (), {})()
        window.events.shown, window.events.loaded, window.events.closing = Event(), Event(), Event()
        bridge = mock.Mock(_shutdown=lambda: order.append("shutdown"))
        receiver = mock.Mock(stop=lambda: order.append("receiver"))
        main._wire_window_events(window, bridge, None, receiver)
        window.events.closing.handlers[0]()
        self.assertEqual(order, ["receiver", "shutdown"])

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


class WindowSettingsTest(unittest.TestCase):
    PRIMARY = ((0, 0, 1920, 1032), 1.0, True)
    SECOND = ((1920, 0, 4480, 1400), 1.5, False)

    def test_window_settings_read_safely(self):
        class Engine:
            def __init__(self, data):
                self.data = data

            def get_settings(self):
                if isinstance(self.data, Exception):
                    raise self.data
                return self.data

        bounds = {"x": 1, "y": 2, "width": 630, "height": 840}
        self.assertEqual(main.window_settings(Engine({"ui_scale": 150, "window_bounds": bounds})), (150, bounds))
        self.assertEqual(main.window_settings(Engine({"ui_scale": "x", "window_bounds": {"x": 1}})), (100, None))
        self.assertEqual(main.window_settings(Engine(RuntimeError("down"))), (100, None))

    def test_initial_args_without_bounds_are_the_start_size(self):
        self.assertEqual(main.initial_window_args(100, None, [self.PRIMARY]), {"width": 630, "height": 840})
        self.assertEqual(main.initial_window_args(100, {"x": 0, "y": 0, "width": 630, "height": 840}, []),
                         {"width": 630, "height": 840})

    def test_initial_args_restore_in_logical_px_of_the_primary_monitor(self):
        bounds = {"x": 300, "y": 80, "width": 700, "height": 900}
        self.assertEqual(main.initial_window_args(100, bounds, [self.PRIMARY]),
                         {"x": 300, "y": 80, "width": 700, "height": 900})
        self.assertEqual(main.initial_window_args(125, bounds, [self.PRIMARY]),
                         {"x": 300, "y": 0, "width": 875, "height": 1032})
        hidpi = ((0, 0, 2880, 1560), 1.5, True)
        self.assertEqual(main.initial_window_args(100, bounds, [hidpi]),
                         {"x": 200, "y": 53, "width": 700, "height": 900})

    def test_initial_args_on_a_missing_monitor_are_centred_on_the_primary(self):
        bounds = {"x": 2200, "y": 100, "width": 630, "height": 840}
        self.assertEqual(main.initial_window_args(100, bounds, [self.PRIMARY]),
                         {"x": 645, "y": 96, "width": 630, "height": 840})
        self.assertEqual(main.initial_window_args(100, bounds, [self.PRIMARY, self.SECOND]),
                         {"x": 2200, "y": 100, "width": 945, "height": 1260})


class FakeHook:
    """The part of winutil.WindowHook that WindowGeometry uses, on a 1920 x 1032 work area at 96 dpi."""

    def __init__(self, rect=(600, 100, 630, 840)):
        self.rect = rect
        self.zoom = 1.0
        self.minimized = False
        self.minimum = main.MIN_SIZE
        self.work = (0, 0, 1920, 1032)

    def client_rect(self):
        return self.rect

    def scale(self):
        return 1.0

    def work_area(self):
        return self.work

    def min_client(self, zoom=None):
        z = self.zoom if zoom is None else zoom
        return round(self.minimum[0] * z), round(self.minimum[1] * z)

    def is_minimized(self):
        return self.minimized

    def place(self, rect, apply=None):
        self.rect = tuple(rect)
        if apply is not None:
            apply(*rect)
        return rect


class FakeBridge:
    def __init__(self):
        self.scales: list = []
        self.applied: list = []
        self.saved: list = []

    def _on_window_scales(self, scales, applied=None):
        self.scales.append(list(scales))
        self.applied.append(applied)

    def _save_window_bounds(self, bounds):
        self.saved.append(bounds)


class WindowGeometryTest(unittest.TestCase):
    def setUp(self):
        self.control = type("Control", (), {"ZoomFactor": 1.0})()
        self.bounds_calls: list = []
        native = type("Native", (), {})()
        native.webview = self.control
        native.SetBounds = lambda *b: self.bounds_calls.append(b)
        self.window = type("Window", (), {"native": native})()
        self.hook = FakeHook()
        self.bridge = FakeBridge()
        self.geometry = main.WindowGeometry(self.window, self.hook, self.bridge, (100, 125, 150, 175, 200))

    def test_first_start_is_the_ladder_size_centred(self):
        with mock.patch("media_forge.shell.winutil.monitors", return_value=[]):
            self.assertEqual(self.geometry.start(100, None), (645, 96, 630, 840))
        self.assertEqual(self.bridge.scales, [[100, 125, 150]])
        self.assertEqual(self.bounds_calls, [(645, 96, 630, 840)])

    def test_start_restores_the_bounds_at_the_scale_step(self):
        monitors = [((0, 0, 1920, 1032), 1.0, True)]
        with mock.patch("media_forge.shell.winutil.monitors", return_value=monitors):
            self.geometry.start(125, {"x": 300, "y": 10, "width": 640, "height": 800})
        self.assertEqual(self.hook.rect, (300, 10, 800, 1000))
        self.assertEqual(self.control.ZoomFactor, 1.25)
        self.assertEqual(self.hook.zoom, 1.25)

    def test_scale_change_keeps_centre_and_proportions_and_saves(self):
        self.hook.rect = (700, 150, 600, 800)
        self.geometry.percent = 125
        self.hook.zoom = 1.25
        self.geometry.set_scale(100)
        self.assertEqual(self.hook.rect, (760, 230, 480, 640))
        self.assertEqual(self.control.ZoomFactor, 1.0)
        self.assertEqual(self.bridge.saved[-1], {"x": 760, "y": 230, "width": 480, "height": 640})
        self.geometry.set_scale(150)
        self.assertEqual(self.hook.rect, (640, 70, 720, 960))
        self.assertEqual(self.bridge.saved[-1], {"x": 640, "y": 70, "width": 480, "height": 640})  # same logical size

    def test_a_stored_step_that_does_not_fit_starts_as_the_largest_that_does(self):
        # Scale 200 % chosen on a large monitor; this one (1920 x 1032 work area at 96 dpi) fits 150 % at most.
        with mock.patch("media_forge.shell.winutil.monitors", return_value=[]):
            self.geometry.start(200, None)
        self.assertEqual((self.geometry.chosen, self.geometry.percent), (200, 150))
        self.assertEqual(self.control.ZoomFactor, 1.5)
        self.assertLessEqual(self.hook.rect[3], 1032)
        self.assertEqual(self.bridge.applied, [150])
        self.assertEqual(self.bridge.saved, [])          # neither the setting nor the bounds are written

    def test_stored_bounds_at_a_step_that_does_not_fit_open_inside_the_work_area(self):
        monitors = [((0, 0, 1920, 1032), 1.0, True)]
        with mock.patch("media_forge.shell.winutil.monitors", return_value=monitors):
            self.geometry.start(200, {"x": 300, "y": 10, "width": 640, "height": 800})
        self.assertEqual(self.geometry.percent, 150)
        self.assertEqual(self.hook.rect, (300, 0, 960, 1032))
        self.assertEqual(self.hook.zoom, 1.5)

    def test_the_monitor_of_the_stored_bounds_decides_the_step(self):
        big = ((1920, 0, 4480, 1400), 1.0, False)       # 2560 x 1400 at 96 dpi: 200 % fits (1280 <= 1400)
        monitors = [((0, 0, 1920, 1032), 1.0, True), big]
        with mock.patch("media_forge.shell.winutil.monitors", return_value=monitors):
            self.geometry.start(200, {"x": 2000, "y": 20, "width": 640, "height": 660})
        self.assertEqual(self.geometry.percent, 200)

    def test_a_display_change_refits_the_step_and_brings_the_chosen_one_back(self):
        self.hook.work = (0, 0, 2560, 1400)
        with mock.patch("media_forge.shell.winutil.monitors", return_value=[]):
            self.geometry.start(200, None)
        self.assertEqual(self.geometry.percent, 200)
        self.hook.work = (0, 0, 1920, 1032)               # moved to a smaller monitor
        self.geometry.on_display()
        self.assertEqual((self.geometry.chosen, self.geometry.percent), (200, 150))
        self.assertEqual(self.control.ZoomFactor, 1.5)
        x, y, w, h = self.hook.rect
        self.assertTrue(y >= 0 and y + h <= 1032 and x + w <= 1920, self.hook.rect)
        self.hook.work = (0, 0, 2560, 1400)               # and back
        self.geometry.on_display()
        self.assertEqual(self.geometry.percent, 200)
        self.assertEqual(self.bridge.applied[-3:], [200, 150, 200])
        self.assertEqual(self.bridge.saved, [])

    def test_scale_change_while_minimized_only_zooms(self):
        self.hook.minimized = True
        self.geometry.set_scale(150)
        self.assertEqual(self.control.ZoomFactor, 1.5)
        self.assertEqual(self.hook.rect, (600, 100, 630, 840))
        self.assertEqual(self.bridge.saved, [])
        self.geometry.save()
        self.assertEqual(self.bridge.saved, [])   # a minimized window keeps its stored bounds


if __name__ == "__main__":
    unittest.main()
