from __future__ import annotations

import os
import unittest
from datetime import date
from unittest.mock import patch
from zoneinfo import ZoneInfo

from trmnl_weekly_calendar import weather_data


class WeatherDataTests(unittest.TestCase):
    def tearDown(self) -> None:
        weather_data.clear_weather_cache()

    def test_nws_forecast_combines_daytime_high_and_nighttime_low(self) -> None:
        payload = {
            "properties": {
                "periods": [
                    {
                        "name": "Saturday",
                        "startTime": "2026-06-13T09:00:00-06:00",
                        "isDaytime": True,
                        "temperature": 76,
                        "temperatureUnit": "F",
                        "shortForecast": "Sunny then Slight Chance Showers And Thunderstorms",
                    },
                    {
                        "name": "Saturday Night",
                        "startTime": "2026-06-13T18:00:00-06:00",
                        "isDaytime": False,
                        "temperature": 50,
                        "temperatureUnit": "F",
                        "shortForecast": "Mostly Cloudy",
                    },
                    {
                        "name": "Sunday",
                        "startTime": "2026-06-14T06:00:00-06:00",
                        "isDaytime": True,
                        "temperature": 60,
                        "temperatureUnit": "F",
                        "shortForecast": "Chance Rain Showers",
                    },
                    {
                        "name": "Sunday Night",
                        "startTime": "2026-06-14T18:00:00-06:00",
                        "isDaytime": False,
                        "temperature": 50,
                        "temperatureUnit": "F",
                        "shortForecast": "Mostly Cloudy",
                    },
                ]
            }
        }

        days = weather_data.days_from_nws_forecast(payload, date(2026, 6, 13), ZoneInfo("America/Denver"))

        self.assertEqual(days[0], ("SAT", "13", "storm", "76 / 50"))
        self.assertEqual(days[1], ("SUN", "14", "rain", "60 / 50"))

    def test_nws_forecast_uses_placeholders_for_days_without_forecasts(self) -> None:
        payload = {
            "properties": {
                "periods": [
                    {
                        "startTime": "2026-06-11T09:00:00-06:00",
                        "isDaytime": True,
                        "temperature": 76,
                        "temperatureUnit": "F",
                        "shortForecast": "Sunny",
                    }
                ]
            }
        }

        days = weather_data.days_from_nws_forecast(payload, date(2026, 6, 7), ZoneInfo("America/Denver"))

        self.assertEqual(days[0], ("SUN", "7", "cloud", "-- / --"))
        self.assertEqual(days[4], ("THU", "11", "clear", "76 / --"))
        self.assertEqual(len(days), 5)

    def test_open_meteo_forecast_uses_daily_temperature_and_weather_code(self) -> None:
        payload = {
            "daily": {
                "time": ["2026-06-13", "2026-06-14", "2026-06-15"],
                "temperature_2m_max": [76.2, 60, 73],
                "temperature_2m_min": [49.6, 50, 56],
                "weather_code": [95, 61, 0],
            }
        }

        days = weather_data.days_from_open_meteo_forecast(payload, date(2026, 6, 13))

        self.assertEqual(days[0], ("SAT", "13", "storm", "76 / 50"))
        self.assertEqual(days[1], ("SUN", "14", "rain", "60 / 50"))
        self.assertEqual(days[2], ("MON", "15", "clear", "73 / 56"))

    def test_weekly_weather_fills_past_days_from_open_meteo_history(self) -> None:
        nws_payload = {
            "properties": {
                "periods": [
                    {
                        "startTime": "2026-06-13T09:00:00-06:00",
                        "isDaytime": True,
                        "temperature": 76,
                        "temperatureUnit": "F",
                        "shortForecast": "Sunny",
                    },
                    {
                        "startTime": "2026-06-13T18:00:00-06:00",
                        "isDaytime": False,
                        "temperature": 50,
                        "temperatureUnit": "F",
                        "shortForecast": "Mostly Cloudy",
                    },
                ]
            }
        }
        history_payload = {
            "daily": {
                "time": ["2026-06-07", "2026-06-08", "2026-06-11"],
                "temperature_2m_max": [88, 76, 70],
                "temperature_2m_min": [51, 49, 47],
                "weather_code": [51, 2, 0],
            }
        }
        urls: list[str] = []

        def fake_fetch_json(url, config, provider=None):
            urls.append(url)
            if "archive-api.open-meteo.com" in url:
                return history_payload
            return nws_payload

        with patch.dict(
            os.environ,
            {
                "TRMNL_WEATHER_PROVIDER": "nws",
                "TRMNL_WEATHER_LAT": "39.772",
                "TRMNL_WEATHER_LON": "-105.231",
                "TRMNL_WEATHER_FORECAST_URL": "https://api.weather.gov/gridpoints/BOU/55,64/forecast",
            },
            clear=True,
        ):
            with patch.object(weather_data, "fetch_json", fake_fetch_json):
                days, source = weather_data.load_weekly_weather(
                    date(2026, 6, 7),
                    tz=ZoneInfo("America/Denver"),
                    today=date(2026, 6, 13),
                )

        self.assertEqual(source, "nws")
        self.assertEqual(days[0], ("SUN", "7", "rain", "88 / 51"))
        self.assertEqual(days[1], ("MON", "8", "partly", "76 / 49"))
        self.assertEqual(days[4], ("THU", "11", "clear", "70 / 47"))
        self.assertEqual(len(days), 5)
        self.assertTrue(any("start_date=2026-06-07" in url for url in urls))
        self.assertTrue(any("end_date=2026-06-11" in url for url in urls))

    def test_load_weekly_weather_returns_none_without_configuration(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            days, source = weather_data.load_weekly_weather(date(2026, 6, 13))

        self.assertIsNone(days)
        self.assertEqual(source, "mock-weather")

    def test_load_weekly_weather_supports_open_meteo_provider(self) -> None:
        payload = {
            "daily": {
                "time": ["2026-06-13"],
                "temperature_2m_max": [76],
                "temperature_2m_min": [50],
                "weather_code": [0],
            }
        }

        with patch.dict(
            os.environ,
            {
                "TRMNL_WEATHER_PROVIDER": "open-meteo",
                "TRMNL_WEATHER_LAT": "39.772",
                "TRMNL_WEATHER_LON": "-105.231",
            },
            clear=True,
        ):
            with patch.object(weather_data, "fetch_json", return_value=payload) as fetch_json:
                days, source = weather_data.load_weekly_weather(
                    date(2026, 6, 13),
                    tz=ZoneInfo("America/Denver"),
                )

        self.assertEqual(source, "open-meteo")
        self.assertEqual(days[0], ("SAT", "13", "clear", "76 / 50"))
        self.assertIn("api.open-meteo.com", fetch_json.call_args.args[0])

    def test_load_weather_forecasts_uses_requested_open_meteo_range(self) -> None:
        payload = {
            "daily": {
                "time": ["2026-06-13", "2026-06-14"],
                "temperature_2m_max": [76, 60],
                "temperature_2m_min": [50, 49],
                "weather_code": [0, 61],
            }
        }

        with patch.dict(
            os.environ,
            {
                "TRMNL_WEATHER_PROVIDER": "open-meteo",
                "TRMNL_WEATHER_LAT": "39.772",
                "TRMNL_WEATHER_LON": "-105.231",
            },
            clear=True,
        ):
            with patch.object(weather_data, "fetch_json", return_value=payload) as fetch_json:
                forecasts, source = weather_data.load_weather_forecasts(
                    date(2026, 6, 13),
                    date(2026, 6, 15),
                    tz=ZoneInfo("America/Denver"),
                )

        self.assertEqual(source, "open-meteo")
        self.assertEqual(forecasts[date(2026, 6, 14)].icon_kind(), "rain")
        url = fetch_json.call_args.args[0]
        self.assertIn("start_date=2026-06-13", url)
        self.assertIn("end_date=2026-06-14", url)

    def test_weather_cache_defaults_to_six_hours(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TRMNL_WEATHER_PROVIDER": "nws",
                "TRMNL_WEATHER_FORECAST_URL": "https://api.weather.gov/gridpoints/BOU/55,64/forecast",
            },
            clear=True,
        ):
            config = weather_data.configured_weather()

        self.assertEqual(config.ttl_seconds, 21600)


if __name__ == "__main__":
    unittest.main()
