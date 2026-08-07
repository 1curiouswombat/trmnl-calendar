from __future__ import annotations

import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from trmnl_weekly_calendar.render import (
    Event,
    H,
    W,
    WEEKLY_DAY_COUNT,
    allocate_timed_event_lanes,
    clipped_segments,
    day_column_edges,
    render_image,
    time_y,
    timed_event_boxes,
)


class RenderLayoutTests(unittest.TestCase):
    def test_overlapping_events_share_day_width(self) -> None:
        layouts = allocate_timed_event_lanes(
            [
                Event(1, 9.0, 10.0, "First"),
                Event(1, 9.5, 10.5, "Second"),
            ]
        )

        self.assertEqual([layout.lane for layout in layouts], [0, 1])
        self.assertEqual([layout.lane_count for layout in layouts], [2, 2])

    def test_touching_events_do_not_share_lanes(self) -> None:
        layouts = allocate_timed_event_lanes(
            [
                Event(1, 9.0, 10.0, "First"),
                Event(1, 10.0, 11.0, "Second"),
            ]
        )

        self.assertEqual([layout.lane for layout in layouts], [0, 0])
        self.assertEqual([layout.lane_count for layout in layouts], [1, 1])

    def test_chained_overlaps_keep_group_lane_count(self) -> None:
        layouts = allocate_timed_event_lanes(
            [
                Event(1, 9.0, 10.0, "First"),
                Event(1, 9.5, 10.5, "Second"),
                Event(1, 10.0, 11.0, "Third"),
            ]
        )

        self.assertEqual([layout.lane for layout in layouts], [0, 1, 0])
        self.assertEqual([layout.lane_count for layout in layouts], [2, 2, 2])

    def test_days_are_allocated_independently(self) -> None:
        layouts = allocate_timed_event_lanes(
            [
                Event(1, 9.0, 10.0, "Monday"),
                Event(2, 9.0, 10.0, "Tuesday"),
            ]
        )

        self.assertEqual([layout.lane for layout in layouts], [0, 0])
        self.assertEqual([layout.lane_count for layout in layouts], [1, 1])

    def test_events_are_clipped_to_visible_hours(self) -> None:
        layouts = allocate_timed_event_lanes(
            [
                Event(1, 5.0, 6.5, "Early"),
                Event(1, 19.5, 21.0, "Late"),
                Event(1, 21.0, 22.0, "Hidden"),
            ]
        )

        self.assertEqual(len(layouts), 2)
        self.assertEqual(
            [(layout.visible_start, layout.visible_end) for layout in layouts],
            [(6.0, 6.5), (19.5, 20.0)],
        )

    def test_clipped_segments_remove_blocked_ranges(self) -> None:
        self.assertEqual(
            clipped_segments(0, 100, [(20, 30), (50, 70)]),
            [(0, 20), (30, 50), (70, 100)],
        )

    def test_current_day_column_is_wider(self) -> None:
        edges = day_column_edges(100, 800, 3)
        widths = [edges[index + 1] - edges[index] for index in range(WEEKLY_DAY_COUNT)]

        self.assertGreater(widths[3], widths[2])
        self.assertEqual(len(edges), WEEKLY_DAY_COUNT + 1)
        self.assertEqual(edges[0], 100)
        self.assertEqual(edges[-1], 800)

    def test_text_can_spill_until_next_conflicting_event(self) -> None:
        layouts = allocate_timed_event_lanes(
            [
                Event(1, 9.0, 9.25, "Short event"),
                Event(1, 9.75, 10.25, "Next event"),
            ]
        )

        boxes = timed_event_boxes(layouts, day_column_edges(100, 800, -1), 10, 8, 300, 900)

        self.assertGreater(boxes[0].text_y1, boxes[0].y1)
        self.assertLess(boxes[0].text_y1, boxes[1].y0)

    def test_now_marker_line_stays_visible_during_active_event(self) -> None:
        now = datetime(2026, 6, 8, 9, 30, tzinfo=ZoneInfo("America/Denver"))
        image = render_image(
            week_start=date(2026, 6, 7),
            all_day_events=[],
            events=[Event(1, 9.0, 10.0, "Active event")],
            now=now,
        )
        grid_top = 46 + 286
        grid_bottom = H - 64
        col_edges = day_column_edges(38 + 72, W - 42, 1)
        marker_x0 = col_edges[1] + 10
        marker_x1 = col_edges[2] - 10
        marker_mid = round((marker_x0 + marker_x1) / 2)
        marker_y = round(time_y(9.5, grid_top, grid_bottom))
        black_pixels = sum(
            1
            for x in range(marker_mid + 20, marker_x1 - 10)
            if image.getpixel((x, marker_y)) == 0
        )

        self.assertGreater(black_pixels, 20)


if __name__ == "__main__":
    unittest.main()
