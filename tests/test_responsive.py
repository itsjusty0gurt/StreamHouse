from __future__ import annotations

import unittest

from ui.responsive import (
    AUTOMATIC_ORIENTATION_RATIO,
    LAYOUT_MODE_LANDSCAPE,
    LAYOUT_MODE_PORTRAIT,
    normalize_layout_mode,
    resolve_orientation,
)


class ResponsiveLayoutTests(unittest.TestCase):
    def test_automatic_mode_uses_window_shape(self) -> None:
        self.assertEqual(resolve_orientation(600, 900), LAYOUT_MODE_PORTRAIT)
        self.assertEqual(resolve_orientation(1200, 700), LAYOUT_MODE_PORTRAIT)
        self.assertEqual(resolve_orientation(1400, 700), LAYOUT_MODE_LANDSCAPE)

    def test_automatic_mode_switches_near_screenshot_ratio(self) -> None:
        height = 1000
        breakpoint_width = int(AUTOMATIC_ORIENTATION_RATIO * height)
        self.assertEqual(
            resolve_orientation(breakpoint_width - 1, height),
            LAYOUT_MODE_PORTRAIT,
        )
        self.assertEqual(
            resolve_orientation(breakpoint_width, height),
            LAYOUT_MODE_LANDSCAPE,
        )

    def test_automatic_mode_has_five_percent_hysteresis(self) -> None:
        self.assertEqual(
            resolve_orientation(1700, 1000, current=LAYOUT_MODE_LANDSCAPE),
            LAYOUT_MODE_LANDSCAPE,
        )
        self.assertEqual(
            resolve_orientation(1800, 1000, current=LAYOUT_MODE_PORTRAIT),
            LAYOUT_MODE_PORTRAIT,
        )
        self.assertEqual(
            resolve_orientation(1600, 1000, current=LAYOUT_MODE_LANDSCAPE),
            LAYOUT_MODE_PORTRAIT,
        )
        self.assertEqual(
            resolve_orientation(1900, 1000, current=LAYOUT_MODE_PORTRAIT),
            LAYOUT_MODE_LANDSCAPE,
        )

    def test_manual_override_wins(self) -> None:
        self.assertEqual(
            resolve_orientation(1200, 600, LAYOUT_MODE_PORTRAIT),
            LAYOUT_MODE_PORTRAIT,
        )
        self.assertEqual(
            normalize_layout_mode("unknown"),
            "automatic",
        )


if __name__ == "__main__":
    unittest.main()
