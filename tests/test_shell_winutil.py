"""Size choice of the fixed window (pure functions, no window) and the harmless Windows helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from media_forge.shell import main, winutil  # noqa: E402
from media_forge.shell.winutil import centered_rect, colorref, pick_window_size  # noqa: E402

LADDER = main.SIZE_LADDER


class WindowSizeTest(unittest.TestCase):
    def test_ladder_is_3_4_largest_first(self):
        self.assertEqual(LADDER, ((630, 840), (600, 800), (570, 760), (540, 720)))
        for w, h in LADDER:
            self.assertEqual(w * 4, h * 3)

    def test_full_size_when_it_fits(self):
        self.assertEqual(pick_window_size(LADDER, (1920, 1040)), (630, 840))
        self.assertEqual(pick_window_size(LADDER, (630, 840)), (630, 840))

    def test_steps_down_for_small_work_areas(self):
        self.assertEqual(pick_window_size(LADDER, (1366, 839)), (600, 800))
        self.assertEqual(pick_window_size(LADDER, (1280, 770)), (570, 760))
        self.assertEqual(pick_window_size(LADDER, (1366, 728)), (540, 720))
        self.assertEqual(pick_window_size(LADDER, (599, 2000)), (570, 760))

    def test_smallest_step_when_nothing_fits_and_first_when_unknown(self):
        self.assertEqual(pick_window_size(LADDER, (500, 600)), (540, 720))
        self.assertEqual(pick_window_size(LADDER, None), (630, 840))
        with self.assertRaises(ValueError):
            pick_window_size((), (1920, 1080))

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


class HarmlessHelpersTest(unittest.TestCase):
    def test_reveal_and_open_missing_path(self):
        self.assertFalse(winutil.reveal_in_explorer(""))
        self.assertFalse(winutil.reveal_in_explorer("Z:\\definitely\\missing\\file.mp4"))
        self.assertFalse(winutil.open_path("Z:\\definitely\\missing\\file.mp4"))

    def test_dwm_without_window(self):
        self.assertFalse(winutil.set_rounded_corners(0))
        self.assertFalse(winutil.set_border_color(0, "#04070f"))

    def test_hook_on_no_window(self):
        hook = winutil.FixedSizeHook(0)
        self.assertFalse(hook.install())
        self.assertEqual(hook.fit_size(LADDER), (630, 840))  # no work area: the first step
        self.assertEqual(hook.lock(630, 840), (0, 0, 630, 840))
        self.assertEqual(hook.size, (630, 840))

    def test_no_resize_code_left(self):
        for name in ("FramelessResizeHook", "aspect_sizing_rect", "fit_start_client", "WM_SIZING"):
            self.assertFalse(hasattr(winutil, name), name)


if __name__ == "__main__":
    unittest.main()
