"""Aspect-lock arithmetic of the frameless window (pure functions, no window) and the harmless Windows helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from media_forge.shell import winutil  # noqa: E402
from media_forge.shell.winutil import (  # noqa: E402
    WMSZ_BOTTOM,
    WMSZ_BOTTOMLEFT,
    WMSZ_BOTTOMRIGHT,
    WMSZ_LEFT,
    WMSZ_RIGHT,
    WMSZ_TOP,
    WMSZ_TOPLEFT,
    WMSZ_TOPRIGHT,
    aspect_sizing_rect,
    fit_aspect_box,
    fit_start_client,
    height_for_width,
    width_for_height,
)

NC = (16, 12)  # invisible side borders 8 + 8, bottom border 8 + top grip 4 (physical px)


def client(rect, nc=NC):
    left, top, right, bottom = rect
    return (right - left) - nc[0], (bottom - top) - nc[1]


def is_3_4(size):
    w, h = size
    return abs(h - w * 4 / 3) <= 1


class RatioTest(unittest.TestCase):
    def test_conversions(self):
        self.assertEqual(height_for_width(630), 840)
        self.assertEqual(width_for_height(840), 630)
        self.assertEqual(height_for_width(540), 720)
        self.assertEqual(height_for_width(788), 1051)  # 630 at 125 % rounds to 788 physical

    def test_fit_aspect_box(self):
        self.assertEqual(fit_aspect_box(630, 2000), (630, 840))
        self.assertEqual(fit_aspect_box(2000, 840), (630, 840))
        self.assertTrue(is_3_4(fit_aspect_box(1001, 1003)))
        w, h = fit_aspect_box(777, 900)
        self.assertLessEqual(w, 777)
        self.assertLessEqual(h, 900)


class StartSizeTest(unittest.TestCase):
    def test_start_size_fits(self):
        self.assertEqual(fit_start_client((630, 840), (540, 720), (1920, 1040), (16, 12)), (630, 840))

    def test_small_work_area_scales_down_keeping_aspect(self):
        w, h = fit_start_client((630, 840), (540, 720), (1366, 728), (16, 12))
        self.assertEqual(h, 716)
        self.assertTrue(is_3_4((w, h)))  # below the minimum: the work area wins

    def test_minimum_kept_when_room(self):
        self.assertEqual(fit_start_client((500, 666), (540, 720), (1920, 1040), (0, 0)), (540, 720))

    def test_unknown_work_area(self):
        self.assertEqual(fit_start_client((630, 840), (540, 720), None), (630, 840))

    def test_narrow_work_area(self):
        w, h = fit_start_client((630, 840), (540, 720), (500, 2000), (16, 12))
        self.assertEqual(w, 484)
        self.assertTrue(is_3_4((w, h)))


class SizingRectTest(unittest.TestCase):
    start = (100, 100, 100 + 630 + NC[0], 100 + 840 + NC[1])

    def drag(self, edge, rect, prev=None, min_client=(540, 720), max_client=None):
        return aspect_sizing_rect(rect, edge, NC, min_client, max_client, prev=prev or self.start)

    def test_right_edge_width_wins_top_left_stays(self):
        proposed = (100, 100, 100 + 700 + NC[0], self.start[3])
        new = self.drag(WMSZ_RIGHT, proposed)
        self.assertEqual(client(new), (700, 933))
        self.assertEqual(new[:2], (100, 100))

    def test_left_edge_moves_left_keeps_right_and_top(self):
        proposed = (40, 100, self.start[2], self.start[3])
        new = self.drag(WMSZ_LEFT, proposed)
        self.assertEqual(new[0], 40)
        self.assertEqual(new[2], self.start[2])
        self.assertEqual(new[1], 100)
        self.assertTrue(is_3_4(client(new)))

    def test_bottom_edge_height_wins(self):
        proposed = (100, 100, self.start[2], 100 + 960 + NC[1])
        new = self.drag(WMSZ_BOTTOM, proposed)
        self.assertEqual(client(new), (720, 960))
        self.assertEqual(new[:2], (100, 100))

    def test_top_edge_keeps_bottom(self):
        proposed = (100, 20, self.start[2], self.start[3])
        new = self.drag(WMSZ_TOP, proposed)
        self.assertEqual(new[3], self.start[3])
        self.assertEqual(new[1], 20)
        self.assertEqual(client(new)[1], 840 + 80)
        self.assertTrue(is_3_4(client(new)))

    def test_corners_anchor_the_opposite_corner(self):
        cases = {
            WMSZ_BOTTOMRIGHT: ((100, 100, self.start[2] + 90, self.start[3] + 10), ("left", "top")),
            WMSZ_TOPLEFT: ((10, 90, self.start[2], self.start[3]), ("right", "bottom")),
            WMSZ_TOPRIGHT: ((100, 90, self.start[2] + 90, self.start[3]), ("left", "bottom")),
            WMSZ_BOTTOMLEFT: ((10, 100, self.start[2], self.start[3] + 10), ("right", "top")),
        }
        index = {"left": 0, "top": 1, "right": 2, "bottom": 3}
        for edge, (proposed, fixed) in cases.items():
            new = self.drag(edge, proposed)
            self.assertTrue(is_3_4(client(new)), edge)
            for side in fixed:
                self.assertEqual(new[index[side]], self.start[index[side]], (edge, side))
            self.assertEqual(client(new)[0], 720, edge)  # the width moved more: it wins

    def test_corner_where_height_moved_more(self):
        proposed = (100, 100, self.start[2] + 5, self.start[3] + 200)
        new = self.drag(WMSZ_BOTTOMRIGHT, proposed)
        self.assertEqual(client(new)[1], 1040)
        self.assertEqual(client(new)[0], 780)

    def test_minimum_enforced(self):
        proposed = (100, 100, 100 + 300 + NC[0], self.start[3])
        new = self.drag(WMSZ_RIGHT, proposed)
        self.assertEqual(client(new), (540, 720))
        proposed = (100, 500, self.start[2], self.start[3])
        new = self.drag(WMSZ_TOP, proposed)
        self.assertEqual(client(new), (540, 720))
        self.assertEqual(new[3], self.start[3])

    def test_maximum_from_work_area(self):
        proposed = (100, 100, 100 + 1200 + NC[0], self.start[3])
        new = self.drag(WMSZ_RIGHT, proposed, max_client=(1904, 1028))
        self.assertEqual(client(new)[1], 1028)
        self.assertTrue(is_3_4(client(new)))

    def test_every_edge_gives_3_4_for_many_sizes(self):
        edges = (WMSZ_LEFT, WMSZ_RIGHT, WMSZ_TOP, WMSZ_TOPLEFT, WMSZ_TOPRIGHT, WMSZ_BOTTOM, WMSZ_BOTTOMLEFT,
                 WMSZ_BOTTOMRIGHT)
        for edge in edges:
            for dw in range(-150, 400, 37):
                for dh in range(-150, 400, 41):
                    proposed = (100, 100, self.start[2] + dw, self.start[3] + dh)
                    new = self.drag(edge, proposed, prev=None)
                    size = client(new)
                    self.assertTrue(is_3_4(size), (edge, dw, dh, size))
                    self.assertGreaterEqual(size[0], 540)

    def test_without_prev_corner_takes_larger_candidate(self):
        proposed = (100, 100, 100 + 900 + NC[0], 100 + 800 + NC[1])
        new = aspect_sizing_rect(proposed, WMSZ_BOTTOMRIGHT, NC, (540, 720), None, prev=None)
        self.assertEqual(client(new), (900, 1200))


class HarmlessHelpersTest(unittest.TestCase):
    def test_reveal_and_open_missing_path(self):
        self.assertFalse(winutil.reveal_in_explorer(""))
        self.assertFalse(winutil.reveal_in_explorer("Z:\\definitely\\missing\\file.mp4"))
        self.assertFalse(winutil.open_path("Z:\\definitely\\missing\\file.mp4"))

    def test_rounded_corners_without_window(self):
        self.assertFalse(winutil.set_rounded_corners(0))

    def test_hook_on_no_window(self):
        hook = winutil.FramelessResizeHook(0, (540, 720), top_grip=4)
        self.assertFalse(hook.install())
        self.assertEqual(hook.set_client_size(630, 840), (0, 0, 0, 0))


if __name__ == "__main__":
    unittest.main()
