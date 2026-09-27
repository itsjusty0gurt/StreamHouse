from __future__ import annotations

import unittest

from shared.streamhouse_shared.responsive import (
    AUTOMATIC_ORIENTATION_RATIO,
    LAYOUT_MODE_LANDSCAPE,
    LAYOUT_MODE_PORTRAIT,
    normalize_layout_mode,
    responsive_grid_columns,
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

    def test_responsive_grid_accounts_for_spacing(self) -> None:
        self.assertEqual(responsive_grid_columns(499, 500, 20), 1)
        self.assertEqual(responsive_grid_columns(1_019, 500, 20), 1)
        self.assertEqual(responsive_grid_columns(1_020, 500, 20), 2)
        self.assertEqual(responsive_grid_columns(1_540, 500, 20), 3)
        self.assertEqual(responsive_grid_columns(2_060, 500, 20), 4)

    def test_responsive_grid_hysteresis_prevents_threshold_churn(self) -> None:
        self.assertEqual(
            responsive_grid_columns(
                1_535,
                500,
                20,
                current=2,
                hysteresis=32,
            ),
            2,
        )
        self.assertEqual(
            responsive_grid_columns(
                1_575,
                500,
                20,
                current=2,
                hysteresis=32,
            ),
            3,
        )
        self.assertEqual(
            responsive_grid_columns(
                1_515,
                500,
                20,
                current=3,
                hysteresis=32,
            ),
            3,
        )
        self.assertEqual(
            responsive_grid_columns(
                1_500,
                500,
                20,
                current=3,
                hysteresis=32,
            ),
            2,
        )


if __name__ == "__main__":
    unittest.main()
