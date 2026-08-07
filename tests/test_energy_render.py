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
    antialiased_series_layer,
    axis_display_unit,
    axis_tick_label,
    CONTENT_BOTTOM,
    DISPLAY_DAYS,
    FONTS,
    GRID_DESCRIPTION,
    H,
    HEADER_BOTTOM,
    PANEL_COUNT,
    W,
    panel_box,
    panel_header_layout,
    panel_kwh,
    panel_plot_box,
    panel_spines,
    panel_totals_text,
    panel_y_max,
    plot_point,
    power_line_segments,
    render_energy_image,
    SERIES_LAYER_PADDING,
    stable_plot_series,
    summary_chart_boxes,
    write_energy_output,
    y_tick_values,
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

    def test_small_multiples_have_detached_spines_without_grid_guides(self) -> None:
        image = render_energy_image(self.data)

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
        left_spine, bottom_spine = panel_spines((px0, py0, px1, py1))
        self.assertEqual(image.getpixel((left_spine[0], py0 + 8)), 105)
        self.assertEqual(image.getpixel(((px0 + px1) // 2, bottom_spine[1])), 105)
        self.assertNotEqual((left_spine[0], left_spine[3]), (bottom_spine[0], bottom_spine[1]))
        self.assertLess(left_spine[0], bottom_spine[0])
        self.assertLess(left_spine[3], bottom_spine[1])
        for hour in (6, 12, 18, 24):
            x = round(px0 + hour / 24 * (px1 - px0))
            self.assertEqual(image.getpixel((x, py0 + 8)), 255)

    def test_compact_header_description_distinguishes_power_and_energy(self) -> None:
        self.assertIn("W/kW AXES", GRID_DESCRIPTION)
        self.assertIn("TODAY / YESTERDAY kWh", GRID_DESCRIPTION)
        self.assertEqual(panel_kwh(12.345), "12.3")
        self.assertEqual(panel_kwh(0.456), "0.46")

        image = Image.new("L", (W, H), 255)
        draw = ImageDraw.Draw(image)
        for index, series in enumerate(stable_plot_series(self.data.series)):
            x0, _, x1, _ = panel_box(index)
            label, totals, label_x, totals_x = panel_header_layout(
                draw, series, panel_box(index)
            )
            label_width = draw.textbbox((0, 0), label, font=FONTS["panel"])[2]
            totals_width = draw.textbbox((0, 0), totals, font=FONTS["panel_value"])[2]

            self.assertEqual(label, label.upper())
            self.assertEqual(totals, panel_totals_text(series))
            self.assertTrue(totals.endswith(" kWh"))
            self.assertAlmostEqual((x0 + x1) / 2, label_x + label_width / 2, delta=1)
            self.assertAlmostEqual((x0 + x1) / 2, totals_x + totals_width / 2, delta=1)

    def test_summary_charts_span_the_full_grid_height(self) -> None:
        energy_box, screen_time_box = summary_chart_boxes()

        self.assertEqual(HEADER_BOTTOM, energy_box[1])
        self.assertEqual(CONTENT_BOTTOM, screen_time_box[3])
        self.assertEqual(energy_box[0], screen_time_box[0])
        self.assertEqual(energy_box[2], screen_time_box[2])

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

    def test_y_axis_uses_five_evenly_spaced_ticks(self) -> None:
        self.assertEqual(
            (0.0, 0.05, 0.1, 0.15, 0.2),
            tuple(round(value, 10) for value in y_tick_values(0.2)),
        )
        self.assertEqual(
            ("0", "2.5", "5", "7.5", "10"),
            tuple(axis_tick_label(value, 0.01) for value in y_tick_values(0.01)),
        )
        self.assertEqual("W", axis_display_unit(0.08))
        self.assertEqual("W", axis_display_unit(0.1))
        self.assertEqual("kW", axis_display_unit(0.15))

    def test_empty_panel_uses_100_watt_default_scale(self) -> None:
        empty = replace(
            stable_plot_series(self.data.series)[0],
            yesterday=(),
            today=(),
        )

        self.assertEqual(0.1, panel_y_max(empty))
        self.assertEqual("W", axis_display_unit(panel_y_max(empty)))

    def test_power_line_segments_split_at_missing_bucket(self) -> None:
        start = datetime(2026, 8, 7, tzinfo=timezone.utc)
        points = (
            AveragePowerPoint(0.125, 1.0, start),
            AveragePowerPoint(0.375, 1.2, start + timedelta(minutes=15)),
            AveragePowerPoint(0.875, 0.8, start + timedelta(minutes=45)),
        )

        segments = power_line_segments(points)

        self.assertEqual([2, 1], [len(segment) for segment in segments])

    def test_trace_layer_has_antialiased_edges_and_preserves_core_colors(self) -> None:
        start = datetime(2026, 8, 7, tzinfo=timezone.utc)
        diagonal = (
            AveragePowerPoint(0, 0.1, start),
            AveragePowerPoint(24, 0.9, start + timedelta(minutes=15)),
        )

        today = antialiased_series_layer((), diagonal, (101, 51), 1.0)
        yesterday = antialiased_series_layer(diagonal, (), (101, 51), 1.0)
        today_values = set(today.get_flattened_data())
        yesterday_values = set(yesterday.get_flattened_data())

        self.assertEqual(
            (101 + SERIES_LAYER_PADDING * 2, 51 + SERIES_LAYER_PADDING * 2),
            today.size,
        )
        self.assertIn(0, today_values)
        self.assertIn(255, today_values)
        self.assertTrue(any(0 < value < 255 for value in today_values))
        self.assertIn(170, yesterday_values)
        self.assertTrue(any(170 < value < 255 for value in yesterday_values))

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
