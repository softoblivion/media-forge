"""Geometry of the resizable window (pure functions, no window) and the harmless Windows helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from media_forge.core.settings import SCALE_STEPS  # noqa: E402
from media_forge.shell import main, winutil  # noqa: E402
from media_forge.shell.winutil import (  # noqa: E402
    bounds_of, centered_rect, clamp_rect, colorref, fitting_scales, pick_window_size, rescaled_rect, restored_rect,
    scale_fits, scaled_size,
)

LADDER = main.SIZE_LADDER
MIN = main.MIN_SIZE
WORK = (0, 0, 1920, 1032)            # a 1920 x 1080 monitor with the taskbar at the bottom
RIGHT = (1920, 0, 4480, 1400)        # a 2560 x 1440 monitor to the right of it, 150 % (work area in physical px)


class WindowSizeTest(unittest.TestCase):
    def test_ladder_is_3_4_largest_first(self):
        self.assertEqual(LADDER, ((630, 840), (600, 800), (570, 760), (540, 720), (510, 680), (480, 640)))
        for w, h in LADDER:
            self.assertEqual(w * 4, h * 3)
        self.assertEqual(main.START_SIZE, (630, 840))
        self.assertEqual(MIN, (480, 640))

    def test_full_size_when_it_fits(self):
        self.assertEqual(pick_window_size(LADDER, (1920, 1040)), (630, 840))
        self.assertEqual(pick_window_size(LADDER, (630, 840)), (630, 840))

    def test_steps_down_for_small_work_areas(self):
        self.assertEqual(pick_window_size(LADDER, (1366, 839)), (600, 800))
        self.assertEqual(pick_window_size(LADDER, (1280, 770)), (570, 760))
        self.assertEqual(pick_window_size(LADDER, (1366, 728)), (540, 720))
        self.assertEqual(pick_window_size(LADDER, (599, 2000)), (570, 760))

    def test_smallest_step_when_nothing_fits_and_first_when_unknown(self):
        self.assertEqual(pick_window_size(LADDER, (500, 600)), (480, 640))
        self.assertEqual(pick_window_size(LADDER, None), (630, 840))
        with self.assertRaises(ValueError):
            pick_window_size((), (1920, 1080))

    def test_scaled_size_must_fit(self):
        # The first start at a Scale step: the largest ladder size whose scaled size fits.
        self.assertEqual(pick_window_size(LADDER, (1920, 1032), 1.0), (630, 840))
        self.assertEqual(pick_window_size(LADDER, (1920, 1032), 1.25), (600, 800))   # 750 x 1000 fits
        self.assertEqual(pick_window_size(LADDER, (1920, 1032), 1.5), (510, 680))    # 765 x 1020 fits
        self.assertEqual(pick_window_size(LADDER, (1920, 1032), 2.0), (480, 640))    # nothing fits: the smallest
        self.assertEqual(pick_window_size(LADDER, (2560, 1400), 1.5), (630, 840))    # 945 x 1260 fits
        self.assertEqual(pick_window_size(LADDER, None, 2.0), (630, 840))

    def test_centered_rect(self):
        self.assertEqual(centered_rect((630, 840), (0, 0, 2560, 1400)), (965, 280, 630, 840))
        self.assertEqual(centered_rect((630, 840), (1920, 0, 3840, 1040)), (2565, 100, 630, 840))
        self.assertEqual(centered_rect((540, 720), (0, 0, 500, 600)), (0, 0, 540, 720))  # top-left stays visible
        self.assertEqual(centered_rect((630, 840), None), (0, 0, 630, 840))

    def test_colorref_of_the_page_background(self):
        self.assertEqual(colorref(main.BACKGROUND), 0x000F0704)
        self.assertEqual(colorref("#ffffff"), 0x00FFFFFF)
        with self.assertRaises(ValueError):
            colorref("#fff")


class ClampTest(unittest.TestCase):
    def test_inside_stays(self):
        self.assertEqual(clamp_rect((100, 50, 700, 900), WORK, MIN), (100, 50, 700, 900))

    def test_below_minimum_grows_to_it(self):
        self.assertEqual(clamp_rect((100, 50, 300, 200), WORK, MIN), (100, 50, 480, 640))
        self.assertEqual(clamp_rect((100, 50, 300, 200), None, MIN), (100, 50, 480, 640))

    def test_larger_than_the_work_area_shrinks_to_it(self):
        self.assertEqual(clamp_rect((-50, -20, 2400, 1400), WORK, MIN), (0, 0, 1920, 1032))

    def test_moved_back_inside(self):
        self.assertEqual(clamp_rect((1700, 900, 630, 840), WORK, MIN), (1290, 192, 630, 840))
        self.assertEqual(clamp_rect((-300, -100, 630, 840), WORK, MIN), (0, 0, 630, 840))
        self.assertEqual(clamp_rect((2500, 100, 945, 1260), RIGHT, MIN), (2500, 100, 945, 1260))

    def test_minimum_wins_over_a_small_work_area(self):
        # 200 % on a 1920 x 1032 work area: the minimum 960 x 1280 is taller than the work area.
        self.assertEqual(clamp_rect((300, 300, 960, 1280), WORK, (960, 1280)), (300, 0, 960, 1280))


class ScaleArithmeticTest(unittest.TestCase):
    def test_ratio_of_the_steps_keeps_centre_and_proportions(self):
        rect = (600, 100, 630, 840)                       # centre (915, 520)
        grown = rescaled_rect(rect, 1.25, WORK, scaled_size(MIN, 1.25))
        self.assertEqual(grown, (521, 0, 788, 1032))  # 788 x 1050 around the centre, then held by the work area
        self.assertEqual(rescaled_rect((600, 100, 630, 840), 150 / 100, (0, 0, 2560, 1400), scaled_size(MIN, 1.5)),
                         (442, 0, 945, 1260))
        x, y, w, h = rescaled_rect((700, 150, 600, 800), 125 / 100, (0, 0, 2560, 1400), scaled_size(MIN, 1.25))
        self.assertEqual((w, h), (750, 1000))
        self.assertEqual((x + w / 2, y + h / 2), (1000, 550))     # the same centre
        self.assertAlmostEqual(w / h, 600 / 800)

    def test_round_trip_down_and_up(self):
        start = (700, 150, 600, 800)
        down = rescaled_rect(start, 100 / 125, WORK, MIN)
        self.assertEqual(down[2:], (480, 640))
        self.assertEqual(rescaled_rect(down, 125 / 100, (0, 0, 2560, 1400), MIN)[2:], (600, 800))

    def test_shrinking_stops_at_the_minimum_of_the_new_step(self):
        self.assertEqual(rescaled_rect((700, 150, 600, 800), 0.5, WORK, MIN)[2:], (480, 640))

    def test_bounds_are_logical_and_independent_of_scale_and_dpi(self):
        self.assertEqual(bounds_of((100, 50, 630, 840), 1.0, 1.0), {"x": 100, "y": 50, "width": 630, "height": 840})
        self.assertEqual(bounds_of((100, 50, 945, 1260), 1.5, 1.0), {"x": 100, "y": 50, "width": 630, "height": 840})
        self.assertEqual(bounds_of((2000, 50, 1181, 1575), 1.25, 1.5),
                         {"x": 2000, "y": 50, "width": 630, "height": 840})

    def test_minimum_per_scale_step(self):
        self.assertEqual([scaled_size(MIN, s / 100) for s in SCALE_STEPS],
                         [(480, 640), (600, 800), (720, 960), (840, 1120), (960, 1280)])
        self.assertTrue(scale_fits(MIN, 1.5, 1.0, WORK))      # 720 x 960 fits 1920 x 1032
        self.assertFalse(scale_fits(MIN, 1.75, 1.0, WORK))    # 840 x 1120 does not
        self.assertFalse(scale_fits(MIN, 1.25, 1.5, WORK))    # 900 x 1200 physical at 150 % DPI
        self.assertTrue(scale_fits(MIN, 2.0, 1.0, None))

    def test_fitting_scales(self):
        self.assertEqual(fitting_scales(SCALE_STEPS, MIN, 1.0, WORK), [100, 125, 150])
        self.assertEqual(fitting_scales(SCALE_STEPS, MIN, 1.0, (0, 0, 2560, 1400)), [100, 125, 150, 175, 200])
        self.assertEqual(fitting_scales(SCALE_STEPS, MIN, 1.5, (0, 0, 1920, 1032)), [100])
        self.assertEqual(fitting_scales(SCALE_STEPS, MIN, 1.0, (0, 0, 400, 500)), [100])  # always one step
        self.assertEqual(fitting_scales(SCALE_STEPS, MIN, 1.0, None), list(SCALE_STEPS))


class RestoreTest(unittest.TestCase):
    PRIMARY = (WORK, 1.0, True)
    SECOND = (RIGHT, 1.5, False)

    def test_restored_where_it_was(self):
        bounds = {"x": 300, "y": 80, "width": 700, "height": 900}
        self.assertEqual(restored_rect(bounds, [self.PRIMARY], 1.0, MIN), ((300, 80, 700, 900), 1.0))
        self.assertEqual(restored_rect(bounds, [self.PRIMARY], 1.25, MIN), ((300, 0, 875, 1032), 1.0))

    def test_on_the_second_monitor_at_its_dpi(self):
        bounds = {"x": 2200, "y": 100, "width": 630, "height": 840}
        rect, dpi = restored_rect(bounds, [self.PRIMARY, self.SECOND], 1.0, MIN)
        self.assertEqual((rect, dpi), ((2200, 100, 945, 1260), 1.5))

    def test_the_monitor_it_overlaps_most_wins(self):
        bounds = {"x": 1700, "y": 100, "width": 630, "height": 840}   # 220 px on the left one, more on the right
        rect, dpi = restored_rect(bounds, [self.PRIMARY, self.SECOND], 1.0, MIN)
        self.assertEqual(dpi, 1.5)
        self.assertEqual(rect, (1920, 100, 945, 1260))               # moved fully onto it

    def test_missing_monitor_opens_centred_on_the_primary(self):
        bounds = {"x": 2200, "y": 100, "width": 630, "height": 840}   # the right monitor is unplugged
        self.assertEqual(restored_rect(bounds, [self.PRIMARY], 1.0, MIN), ((645, 96, 630, 840), 1.0))
        secondary_first = [((-1920, 0, 0, 1040), 1.0, False), self.PRIMARY]
        self.assertEqual(restored_rect(bounds, secondary_first, 1.0, MIN), ((645, 96, 630, 840), 1.0))

    def test_too_small_or_too_large_bounds_are_clamped(self):
        self.assertEqual(restored_rect({"x": 10, "y": 10, "width": 100, "height": 100}, [self.PRIMARY], 1.0, MIN),
                         ((10, 10, 480, 640), 1.0))
        self.assertEqual(restored_rect({"x": 0, "y": 0, "width": 5000, "height": 5000}, [self.PRIMARY], 1.0, MIN),
                         ((0, 0, 1920, 1032), 1.0))

    def test_no_monitors(self):
        self.assertIsNone(restored_rect({"x": 0, "y": 0, "width": 630, "height": 840}, [], 1.0, MIN))


class HarmlessHelpersTest(unittest.TestCase):
    def test_reveal_and_open_missing_path(self):
        self.assertFalse(winutil.reveal_in_explorer(""))
        self.assertFalse(winutil.reveal_in_explorer("Z:\\definitely\\missing\\file.mp4"))
        self.assertFalse(winutil.open_path("Z:\\definitely\\missing\\file.mp4"))

    def test_dwm_without_window(self):
        self.assertFalse(winutil.set_rounded_corners(0))
        self.assertFalse(winutil.set_border_color(0, "#04070f"))

    def test_hook_on_no_window(self):
        hook = winutil.WindowHook(0, MIN)
        self.assertFalse(hook.install())
        self.assertIsNone(hook.client_rect())
        self.assertEqual(hook.insets(), (0, 0, 0, 0))
        self.assertEqual(hook.min_client(), (480, 640))
        hook.zoom = 1.5
        self.assertEqual(hook.min_client(), (720, 960))
        self.assertEqual(hook.min_client(2.0), (960, 1280))
        self.assertFalse(hook.begin_drag("top"))
        self.assertFalse(hook.begin_drag("move"))
        self.assertFalse(hook.is_minimized())
        self.assertEqual(hook.place((10, 20, 630, 840)), (10, 20, 630, 840))

    def test_style_bits(self):
        style = winutil.WindowHook._fixed_style(0x16CF0000)  # WS_OVERLAPPEDWINDOW | WS_VISIBLE | WS_CLIPCHILDREN
        self.assertTrue(style & winutil.WS_THICKFRAME)
        self.assertTrue(style & winutil.WS_MINIMIZEBOX)
        self.assertFalse(style & winutil.WS_MAXIMIZEBOX)
        self.assertEqual(winutil.DRAG_PARTS, {"move": 2, "top": 12, "top-left": 13, "top-right": 14})

    def test_fixed_size_code_is_gone(self):
        for name in ("FixedSizeHook", "placed_rect", "window_position"):
            self.assertFalse(hasattr(winutil, name), name)


@unittest.skipUnless(sys.platform == "win32", "Win32 window")
class LiveHookTest(unittest.TestCase):
    """The hook on a real (hidden) top-level window of this thread: messages sent here reach its procedure at once.
    This process is DPI-unaware, so the window's own DPI stays 96 and only the message's DPI changes."""

    def setUp(self):
        import ctypes
        from ctypes import wintypes

        self.ctypes, self.wintypes = ctypes, wintypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.CreateWindowExW.restype = wintypes.HWND
        self.user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                                wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        self.user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user32.SendMessageW.restype = ctypes.c_ssize_t
        self.user32.DestroyWindow.argtypes = [wintypes.HWND]
        self.user32.IsZoomed.argtypes = [wintypes.HWND]
        self.hwnd = self.user32.CreateWindowExW(0, "STATIC", "hook test", 0x80000000,  # WS_POPUP, not visible
                                                100, 100, 500, 700, None, None, None, None)
        self.assertTrue(self.hwnd)
        self.hook = winutil.WindowHook(self.hwnd, MIN)
        self.assertTrue(self.hook.install())

    def tearDown(self):
        self.user32.DestroyWindow(self.hwnd)

    def test_frame_minimum_and_maximize(self):
        left, top, right, bottom = self.hook.insets()
        self.assertEqual(top, 0)                                    # the page owns the top edge
        self.assertGreater(left, 0)
        self.assertEqual((left, bottom), (right, right))           # the sizing border on the other three sides
        self.hook.place((200, 100, 300, 300))
        self.assertEqual(self.hook.client_rect(), (200, 100, 480, 640))
        self.hook.place((200, 100, 600, 700))
        self.assertEqual(self.hook.client_rect(), (200, 100, 600, 700))
        self.user32.SendMessageW(self.hwnd, winutil.WM_SYSCOMMAND, winutil.SC_MAXIMIZE, 0)
        self.user32.SendMessageW(self.hwnd, winutil.WM_NCLBUTTONDBLCLK, 2, 0)
        self.assertFalse(self.user32.IsZoomed(self.hwnd))
        self.assertEqual(self.hook.client_rect(), (200, 100, 600, 700))

    def test_dpi_change_keeps_the_logical_size(self):
        self.hook.place((200, 100, 600, 700))
        rect = self.wintypes.RECT(300, 50, 1050, 925)               # where Windows suggests the window at 120 dpi
        self.user32.SendMessageW(self.hwnd, winutil.WM_DPICHANGED, (120 << 16) | 120,
                                 self.ctypes.addressof(rect))
        x, y, w, h = self.hook.client_rect()
        self.assertEqual((w, h), (750, 875))                        # 600 x 700 at 125 %
        self.assertEqual(y, 50)                                     # at the suggested top (no top border)
        back = self.wintypes.RECT(300, 50, 900, 750)
        self.user32.SendMessageW(self.hwnd, winutil.WM_DPICHANGED, (96 << 16) | 96, self.ctypes.addressof(back))
        self.assertEqual(self.hook.client_rect()[2:], (600, 700))


if __name__ == "__main__":
    unittest.main()
