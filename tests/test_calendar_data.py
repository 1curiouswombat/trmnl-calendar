from __future__ import annotations

import os
import unittest
from datetime import date
from unittest.mock import patch
from zoneinfo import ZoneInfo

from trmnl_weekly_calendar import calendar_data


class CalendarDataTests(unittest.TestCase):
    def test_configured_calendar_sources_support_labels_and_account_placeholder(self) -> None:
        with patch.dict(
            os.environ,
            {"TRMNL_GOG_CALENDARS": "Peter St. John={account}, Corbin=Corbin, Family=Family"},
        ):
            sources = calendar_data.configured_calendar_sources("person@example.com")

        self.assertEqual(
            sources,
            [
                calendar_data.CalendarSource("Peter St. John", "person@example.com"),
                calendar_data.CalendarSource("Corbin", "Corbin"),
                calendar_data.CalendarSource("Family", "Family"),
            ],
        )

    def test_load_gog_events_tags_each_configured_calendar(self) -> None:
        calls: list[str | None] = []

        def fake_run_gog(command_template, range_start, range_end, calendar_id):
            calls.append(calendar_id)
            return [{"summary": f"{calendar_id} event", "start": {"date": "2026-06-08"}}]

        with patch.dict(os.environ, {"TRMNL_GOG_CALENDARS": "Corbin=corbin-id, Family=family-id"}):
            with patch.object(calendar_data, "run_gog", fake_run_gog):
                events = calendar_data.load_gog_events("gog events {calendar}", date(2026, 6, 8), date(2026, 6, 9))

        self.assertEqual(calls, ["corbin-id", "family-id"])
        self.assertEqual([event["_trmnl_calendar_label"] for event in events], ["Corbin", "Family"])
        self.assertEqual([event["_trmnl_calendar_id"] for event in events], ["corbin-id", "family-id"])

    def test_weekly_event_tones_follow_calendar_label(self) -> None:
        raw_events = [
            {
                "summary": "Personal",
                "start": {"dateTime": "2026-06-08T09:00:00-06:00"},
                "end": {"dateTime": "2026-06-08T10:00:00-06:00"},
                "_trmnl_calendar_label": "Peter St. John",
            },
            {
                "summary": "Kid",
                "start": {"dateTime": "2026-06-08T10:00:00-06:00"},
                "end": {"dateTime": "2026-06-08T11:00:00-06:00"},
                "_trmnl_calendar_label": "Corbin",
            },
            {
                "summary": "Family",
                "start": {"date": "2026-06-08"},
                "end": {"date": "2026-06-09"},
                "_trmnl_calendar_label": "Family",
            },
        ]

        timed, all_day = calendar_data.parse_events(raw_events, date(2026, 6, 7), ZoneInfo("America/Denver"))

        self.assertEqual([event.tone for event in timed], [232, 238])
        self.assertEqual([event.tone for event in all_day], [226])

    def test_weekly_events_are_limited_to_five_day_window(self) -> None:
        raw_events = [
            {
                "summary": "Visible timed",
                "start": {"dateTime": "2026-06-11T09:00:00-06:00"},
                "end": {"dateTime": "2026-06-11T10:00:00-06:00"},
            },
            {
                "summary": "Hidden timed",
                "start": {"dateTime": "2026-06-12T09:00:00-06:00"},
                "end": {"dateTime": "2026-06-12T10:00:00-06:00"},
            },
            {
                "summary": "Visible all-day",
                "start": {"date": "2026-06-10"},
                "end": {"date": "2026-06-13"},
            },
        ]

        timed, all_day = calendar_data.parse_events(raw_events, date(2026, 6, 7), ZoneInfo("America/Denver"))

        self.assertEqual([(event.day, event.title) for event in timed], [(4, "Visible timed")])
        self.assertEqual([(event.start_day, event.end_day, event.title) for event in all_day], [(3, 4, "Visible all-day")])

    def test_month_event_tones_follow_calendar_label(self) -> None:
        raw_events = [
            {
                "summary": "Kid",
                "start": {"dateTime": "2026-06-08T10:00:00-06:00"},
                "end": {"dateTime": "2026-06-08T11:00:00-06:00"},
                "_trmnl_calendar_label": "Corbin",
            }
        ]

        events = calendar_data.parse_month_events(
            raw_events,
            date(2026, 6, 1),
            date(2026, 7, 1),
            ZoneInfo("America/Denver"),
        )

        self.assertEqual([event.tone for event in events], [238])


if __name__ == "__main__":
    unittest.main()
