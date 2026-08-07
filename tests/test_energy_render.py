from __future__ import annotations

import struct
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw

from trmnl_weekly_calendar.energy_data import AveragePowerPoint, mock_energy_dashboard
from trmnl_weekly_calendar.energy_render import (
    DISPLAY_DAYS,
    FONTS,
    GRID_SUBTITLE,
    H,
    PANEL_COUNT,
    W,
    panel_box,
    panel_header_layout,
    panel_kwh,
    panel_plot_box,
    panel_totals_text,
    panel_y_max,
    plot_point,
    power_line_segments,
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

    def test_panels_sort_by_month_usage_with_total_pinned_first(self) -> None:
        series = stable_plot_series(self.data.series)

        self.assertEqual(len(series), PANEL_COUNT)
        self.assertEqual(series[0].key, "total")
        self.assertEqual(
            [item.month_kwh for item in series[1:]],
            sorted((item.month_kwh for item in series[1:]), reverse=True),
        )
        self.assertEqual(len({panel_box(index) for index in range(PANEL_COUNT)}), PANEL_COUNT)

    def test_month_usage_ties_have_deterministic_label_and_key_order(self) -> None:
        total = next(item for item in self.data.series if item.key == "total")
        tied = [
            replace(item, month_kwh=1.0)
            for item in reversed(self.data.series)
            if item.key != "total"
        ]

        ordered = stable_plot_series([*tied, total])

        self.assertEqual(
            [(item.label.casefold(), item.key) for item in ordered[1:]],
            sorted((item.label.casefold(), item.key) for item in tied),
        )

    def test_small_multiples_are_despined_without_legend_or_grid_guides(self) -> None:
        image = render_energy_image(self.data)

        self.assertEqual(image.crop((1410, 103, 1780, 140)).getextrema(), (255, 255))
        for index in range(PANEL_COUNT):
            box = panel_box(index)
            corners = (
                (box[0], box[1]),
                (box[2], box[1]),
                (box[0], box[3]),
                (box[2], box[3]),
            )
            self.assertTrue(all(image.getpixel(corner) == 255 for corner in corners))

        px0, py0, px1, py1 = panel_plot_box(panel_box(0))
        self.assertEqual(image.getpixel((px0, py0 + 8)), 105)
        self.assertEqual(image.getpixel(((px0 + px1) // 2, py1)), 105)
        for hour in (6, 12, 18, 24):
            x = round(px0 + hour / 24 * (px1 - px0))
            self.assertEqual(image.getpixel((x, py0 + 8)), 255)

        self.assertEqual(image.getpixel((1410, 164)), 255)
        self.assertEqual(image.getpixel((1428, 244)), 105)

    def test_grid_distinguishes_plot_power_from_daily_energy_totals(self) -> None:
        self.assertEqual(
            GRID_SUBTITLE,
            "15-MIN AVERAGE POWER • kW • DAILY VALUES: TODAY / YESTERDAY kWh",
        )
        self.assertEqual(panel_kwh(12.345), "12.3")
        self.assertEqual(panel_kwh(0.456), "0.46")

        image = Image.new("L", (W, H), 255)
        draw = ImageDraw.Draw(image)
        for index, series in enumerate(stable_plot_series(self.data.series)):
            x0, _, _, _ = panel_box(index)
            label, totals, totals_x = panel_header_layout(
                draw, series, panel_box(index)
            )
            label_width = draw.textbbox((0, 0), label, font=FONTS["panel"])[2]

            self.assertEqual(totals, panel_totals_text(series))
            self.assertTrue(totals.endswith(" kWh"))
            self.assertLessEqual(x0 + 10 + label_width + 8, totals_x)

    def test_today_line_stops_at_latest_sample(self) -> None:
        total = stable_plot_series(self.data.series)[0]
        plot = (0, 0, 240, 100)

        today_x, _ = plot_point(total.today[-1], plot, panel_y_max(total))
        yesterday_x, _ = plot_point(total.yesterday[-1], plot, panel_y_max(total))

        self.assertLess(today_x, yesterday_x)
        self.assertLess(total.today[-1].hour, 24)
        self.assertLess(total.yesterday[-1].hour, 24)
        self.assertGreater(total.yesterday[-1].hour, 23.5)

    def test_power_plot_uses_zero_baseline_and_independent_readable_scale(self) -> None:
        series = stable_plot_series(self.data.series)
        low_use = min(series[1:-1], key=lambda item: item.yesterday_kwh)
        plot = (10, 20, 250, 120)

        baseline = AveragePowerPoint(0, 0, datetime(2026, 8, 7, tzinfo=timezone.utc))
        self.assertEqual(
            plot_point(baseline, plot, panel_y_max(low_use)), (10, 120)
        )
        self.assertNotEqual(panel_y_max(series[0]), panel_y_max(low_use))
        self.assertLess(panel_y_max(low_use), 1)

    def test_power_line_segments_split_at_missing_bucket(self) -> None:
        start = datetime(2026, 8, 7, tzinfo=timezone.utc)
        points = (
            AveragePowerPoint(0.125, 1.0, start),
            AveragePowerPoint(0.375, 1.2, start + timedelta(minutes=15)),
            AveragePowerPoint(0.875, 0.8, start + timedelta(minutes=45)),
        )

        segments = power_line_segments(points)

        self.assertEqual([2, 1], [len(segment) for segment in segments])

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
