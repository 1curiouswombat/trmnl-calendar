from __future__ import annotations

import struct
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from trmnl_weekly_calendar.energy_data import CumulativeEnergyPoint, mock_energy_dashboard
from trmnl_weekly_calendar.energy_render import (
    DISPLAY_DAYS,
    H,
    PANEL_COUNT,
    W,
    panel_box,
    panel_y_max,
    plot_point,
    render_energy_image,
    stable_plot_series,
    write_energy_output,
)


NOW = datetime(2026, 8, 7, 19, 30, tzinfo=ZoneInfo("America/Denver"))


class EnergyRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = mock_energy_dashboard(NOW)

    def test_mock_renders_target_grayscale_canvas(self) -> None:
        image = render_energy_image(self.data)

        self.assertEqual(image.size, (W, H))
        self.assertEqual(image.mode, "L")

    def test_panel_order_and_grid_are_stable(self) -> None:
        series = stable_plot_series(self.data.series)

        self.assertEqual(len(series), PANEL_COUNT)
        self.assertEqual(series[0].key, "total")
        self.assertEqual(series[-1].key, "balance")
        self.assertEqual(len({panel_box(index) for index in range(PANEL_COUNT)}), PANEL_COUNT)

    def test_today_line_stops_at_latest_sample(self) -> None:
        total = stable_plot_series(self.data.series)[0]
        plot = (0, 0, 240, 100)

        today_x, _ = plot_point(total.today[-1], plot, panel_y_max(total))
        yesterday_x, _ = plot_point(total.yesterday[-1], plot, panel_y_max(total))

        self.assertLess(today_x, yesterday_x)
        self.assertLess(total.today[-1].hour, 24)
        self.assertEqual(total.yesterday[-1].hour, 24)

    def test_cumulative_plot_uses_zero_baseline_and_independent_scale(self) -> None:
        series = stable_plot_series(self.data.series)
        low_use = min(series[1:-1], key=lambda item: item.yesterday_kwh)
        plot = (10, 20, 250, 120)

        self.assertEqual(plot_point(CumulativeEnergyPoint(0, 0), plot, panel_y_max(low_use)), (10, 120))
        self.assertNotEqual(panel_y_max(series[0]), panel_y_max(low_use))

    def test_daily_charts_have_seven_values_and_today_is_partial(self) -> None:
        self.assertEqual(len(self.data.daily_mains), DISPLAY_DAYS)
        self.assertEqual(len(self.data.daily_tv_hours), DISPLAY_DAYS)
        self.assertEqual(self.data.daily_mains[-1].day, self.data.today)
        self.assertFalse(self.data.daily_mains[-1].complete)
        self.assertFalse(self.data.daily_tv_hours[-1].complete)

    def test_output_is_true_four_bit_grayscale_png(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = write_energy_output(
                render_energy_image(self.data), Path(temporary) / "energy.png"
            )
            payload = path.read_bytes()
            width, height, bit_depth, color_type = struct.unpack(">IIBB", payload[16:26])

        self.assertEqual((width, height), (W, H))
        self.assertEqual(bit_depth, 4)
        self.assertEqual(color_type, 0)


if __name__ == "__main__":
    unittest.main()
