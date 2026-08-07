from __future__ import annotations

import os
import time
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch
from zoneinfo import ZoneInfo

from PIL import Image

from trmnl_weekly_calendar import server


class ServerRenderTests(unittest.TestCase):
    def test_weekly_render_starts_on_generated_date(self) -> None:
        captured = {}
        generated_at = datetime(2026, 6, 13, 9, 30, tzinfo=ZoneInfo("America/Denver"))

        def fake_render_image(**kwargs):
            captured.update(kwargs)
            return object()

        with patch.object(server, "load_events", return_value=([], [], "calendar")) as load_events:
            with patch.object(server, "load_weekly_weather", return_value=(None, "weather")) as load_weather:
                with patch.object(server, "render_image", fake_render_image):
                    image, source = server.render_weekly(generated_at)

        self.assertIsNotNone(image)
        self.assertEqual(source, "calendar+weather")
        self.assertEqual(load_events.call_args.args[0].isoformat(), "2026-06-13")
        self.assertEqual(load_weather.call_args.args[0].isoformat(), "2026-06-13")
        self.assertEqual(captured["week_start"].isoformat(), "2026-06-13")
        self.assertEqual(captured["days"][0][:2], ("SAT", "13"))
        self.assertEqual(captured["days"][-1][:2], ("WED", "17"))

    def test_month_render_starts_on_current_week(self) -> None:
        captured = {}
        generated_at = datetime(2026, 6, 30, 9, 30, tzinfo=ZoneInfo("America/Denver"))

        def fake_render_month_image(**kwargs):
            captured.update(kwargs)
            return object()

        with patch.object(server, "load_month_events", return_value=([], "calendar")) as load_month_events:
            with patch.object(server, "load_weather_forecasts", return_value=(None, "weather")):
                with patch.object(server, "render_month_image", fake_render_month_image):
                    image, source = server.render_month(generated_at)

        self.assertIsNotNone(image)
        self.assertEqual(source, "calendar+weather")
        self.assertEqual(load_month_events.call_args.args[0].isoformat(), "2026-06-28")
        self.assertEqual(load_month_events.call_args.kwargs["range_end"].isoformat(), "2026-07-26")
        self.assertEqual(captured["view_start"].isoformat(), "2026-06-28")

    def test_calendar_cache_returns_stale_while_refreshing(self) -> None:
        render_started = Event()
        release_render = Event()
        render_count = 0

        def render(_generated_at, _force):
            nonlocal render_count
            render_count += 1
            if render_count == 2:
                render_started.set()
                release_render.wait(2)
            return Image.new("L", (2, 2), color=render_count * 80), f"render-{render_count}"

        with TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"TRMNL_PERSIST_RENDER_CACHE": "0"}):
                cache = server.CalendarCache(
                    server.CalendarPlugin("test", "test", render),
                    refresh_seconds=1,
                    cache_dir=Path(temp_dir),
                )
                with patch.object(server.time, "time", return_value=1000):
                    first = cache.get()
                with patch.object(server.time, "time", return_value=1002):
                    second = cache.get()

                self.assertTrue(render_started.wait(2))
                self.assertEqual(second.fingerprint, first.fingerprint)
                release_render.set()
                for _ in range(20):
                    if cache._rendered and cache._rendered.fingerprint != first.fingerprint:
                        break
                    time.sleep(0.05)
                with patch.object(server.time, "time", return_value=1002):
                    third = cache.get()

        self.assertNotEqual(third.fingerprint, first.fingerprint)

    def test_calendar_cache_loads_persisted_render(self) -> None:
        def render(_generated_at, _force):
            return Image.new("L", (2, 2), color=7), "persisted-source"

        with TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"TRMNL_PERSIST_RENDER_CACHE": "1"}):
                plugin = server.CalendarPlugin("persisted", "persisted", render)
                with patch.object(server.time, "time", return_value=2000):
                    first_cache = server.CalendarCache(plugin, refresh_seconds=10, cache_dir=Path(temp_dir))
                    rendered = first_cache.get()

                def fail_render(_generated_at, _force):
                    raise AssertionError("render should not be called")

                loaded_cache = server.CalendarCache(
                    server.CalendarPlugin("persisted", "persisted", fail_render),
                    refresh_seconds=10,
                    cache_dir=Path(temp_dir),
                )
                with patch.object(server.time, "time", return_value=2000):
                    loaded = loaded_cache.get()

        self.assertEqual(loaded.fingerprint, rendered.fingerprint)
        self.assertEqual(loaded.body, rendered.body)

    def test_seconds_until_next_prewarm_waits_until_after_next_bucket(self) -> None:
        self.assertEqual(server.seconds_until_next_prewarm(1000.0, 900, 5.0), 805.0)
        self.assertEqual(server.seconds_until_next_prewarm(1799.0, 900, 5.0), 6.0)
        self.assertEqual(server.seconds_until_next_prewarm(1804.5, 900, 5.0), 1.0)
        self.assertEqual(server.seconds_until_next_prewarm(1805.5, 900, 5.0), 899.5)


if __name__ == "__main__":
    unittest.main()
