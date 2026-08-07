from __future__ import annotations

import json
import os
import stat
import time
import unittest
from datetime import datetime
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from unittest.mock import Mock, patch
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

    def test_energy_render_loads_real_dashboard_data_and_forwards_force(self) -> None:
        generated_at = datetime(2026, 8, 7, 9, 30, tzinfo=ZoneInfo("America/Denver"))
        dashboard = Mock(source="emporia")
        rendered_image = object()

        with patch.object(server, "load_energy_dashboard", return_value=dashboard) as load:
            with patch.object(server, "render_energy_image", return_value=rendered_image) as render:
                image, source = server.render_energy(generated_at, force=True)

        self.assertIs(image, rendered_image)
        self.assertEqual(source, "emporia")
        load.assert_called_once_with(force=True, now=generated_at)
        render.assert_called_once_with(dashboard)

    def test_plugin_specs_registers_energy_dashboard(self) -> None:
        plugins = server.plugin_specs()

        self.assertEqual(set(plugins), {"weekly", "month", "energy"})
        self.assertEqual(plugins["energy"].filename_prefix, "energy-dashboard")
        self.assertIs(plugins["energy"].render, server.render_energy)

    def test_energy_routes_and_legacy_aliases_resolve(self) -> None:
        self.assertEqual(
            server.resolve_route("/energy/trmnl.json"),
            server.Route("json", "energy", "/energy/image.png"),
        )
        for path in ("/energy/image.png", "/energy/calendar.png"):
            self.assertEqual(
                server.resolve_route(path),
                server.Route("image", "energy", "/energy/image.png"),
            )

        self.assertEqual(
            server.resolve_route("/trmnl.json"),
            server.Route("json", "weekly", "/image.png"),
        )
        self.assertEqual(
            server.resolve_route("/image.png"),
            server.Route("image", "weekly", "/image.png"),
        )
        self.assertEqual(
            server.resolve_route("/month/trmnl.json"),
            server.Route("json", "month", "/month/image.png"),
        )

    def test_energy_redirect_and_image_use_shared_cache_contract(self) -> None:
        body = b"\x89PNG\r\nenergy"
        rendered = server.RenderedCalendar(
            bucket=1,
            body=body,
            fingerprint="1234abcd",
            source="emporia",
            generated_at=datetime(2026, 8, 7, tzinfo=ZoneInfo("America/Denver")),
        )

        class FakeCache:
            plugin = server.CalendarPlugin("energy", "energy-dashboard", server.render_energy)
            refresh_seconds = 900

            def __init__(self) -> None:
                self.force_calls: list[bool] = []

            def get(self, *, force: bool = False) -> server.RenderedCalendar:
                self.force_calls.append(force)
                return rendered

        cache = FakeCache()
        http_server = server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            server.make_handler({"energy": cache}),
        )
        thread = Thread(target=http_server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection("127.0.0.1", http_server.server_port, timeout=2)
        try:
            connection.request("GET", "/energy/trmnl.json")
            response = connection.getresponse()
            redirect_body = response.read()
            payload = json.loads(redirect_body)
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Content-Type"), "application/json")
            self.assertEqual(int(response.getheader("Content-Length")), len(redirect_body))
            self.assertEqual(response.getheader("Cache-Control"), "no-store")
            self.assertEqual(payload["filename"], "energy-dashboard-1234abcd")
            self.assertEqual(payload["refresh_rate"], 900)
            self.assertEqual(
                payload["url"],
                f"http://127.0.0.1:{http_server.server_port}/energy/image.png?v=1234abcd",
            )

            connection.request("GET", "/energy/calendar.png?refresh=1")
            response = connection.getresponse()
            image_body = response.read()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Content-Type"), "image/png")
            self.assertEqual(int(response.getheader("Content-Length")), len(body))
            self.assertEqual(response.getheader("Cache-Control"), "no-store")
            self.assertEqual(image_body, body)
        finally:
            connection.close()
            http_server.shutdown()
            http_server.server_close()
            thread.join(timeout=2)

        self.assertEqual(cache.force_calls, [False, True])

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
                body_mode = stat.S_IMODE((Path(temp_dir) / "persisted.png").stat().st_mode)
                metadata_mode = stat.S_IMODE((Path(temp_dir) / "persisted.json").stat().st_mode)

        self.assertEqual(loaded.fingerprint, rendered.fingerprint)
        self.assertEqual(loaded.body, rendered.body)
        self.assertEqual(body_mode, 0o600)
        self.assertEqual(metadata_mode, 0o600)

    def test_seconds_until_next_prewarm_waits_until_after_next_bucket(self) -> None:
        self.assertEqual(server.seconds_until_next_prewarm(1000.0, 900, 5.0), 805.0)
        self.assertEqual(server.seconds_until_next_prewarm(1799.0, 900, 5.0), 6.0)
        self.assertEqual(server.seconds_until_next_prewarm(1804.5, 900, 5.0), 1.0)
        self.assertEqual(server.seconds_until_next_prewarm(1805.5, 900, 5.0), 899.5)


if __name__ == "__main__":
    unittest.main()
