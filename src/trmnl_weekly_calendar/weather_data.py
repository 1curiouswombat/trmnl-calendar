from __future__ import annotations

import json
import os
import time as time_module
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from threading import RLock
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from trmnl_weekly_calendar.constants import WEEKLY_DAY_COUNT
from trmnl_weekly_calendar.external_api_log import duration_ms, log_http_call, perf_counter


WeatherDays = list[tuple[str, str, str, str]]

DEFAULT_NWS_API_BASE = "https://api.weather.gov"
DEFAULT_OPEN_METEO_API_URL = "https://api.open-meteo.com/v1/forecast"
DEFAULT_OPEN_METEO_ARCHIVE_API_URL = "https://archive-api.open-meteo.com/v1/archive"
DEFAULT_NWS_USER_AGENT = "trmnl-calendar (personal TRMNL display)"
DEFAULT_WEATHER_TTL_SECONDS = 6 * 60 * 60
DEFAULT_POINT_TTL_SECONDS = 7 * 24 * 60 * 60
DEFAULT_TIMEOUT_SECONDS = 1.5
MISSING_TEMP_LABEL = "-- / --"


@dataclass(frozen=True)
class WeatherConfig:
    provider: str
    latitude: str
    longitude: str
    forecast_url: str
    api_base: str
    open_meteo_api_url: str
    open_meteo_archive_api_url: str
    user_agent: str
    timeout_seconds: float
    ttl_seconds: int
    point_ttl_seconds: int


@dataclass(frozen=True)
class WeatherForecastKey:
    config: WeatherConfig
    source: str
    range_start: date
    range_end: date
    timezone: str


@dataclass(frozen=True)
class PointKey:
    api_base: str
    latitude: str
    longitude: str
    user_agent: str


@dataclass
class WeatherForecastCacheEntry:
    expires_at: float
    forecasts: dict[date, DailyForecast]


@dataclass
class PointCacheEntry:
    expires_at: float
    forecast_url: str


@dataclass
class DailyForecast:
    high: int | None = None
    low: int | None = None
    day_icon: str | None = None
    night_icon: str | None = None

    def has_temperature(self) -> bool:
        return self.high is not None or self.low is not None

    def icon_kind(self) -> str:
        return self.day_icon or self.night_icon or "cloud"


class WeatherCache:
    def __init__(self) -> None:
        self._lock = RLock()
        self._forecasts: dict[WeatherForecastKey, WeatherForecastCacheEntry] = {}
        self._points: dict[PointKey, PointCacheEntry] = {}

    def load_days(
        self,
        config: WeatherConfig,
        week_start: date,
        tz: ZoneInfo,
        *,
        force: bool = False,
        today: date | None = None,
    ) -> WeatherDays:
        range_end = week_start + timedelta(days=WEEKLY_DAY_COUNT)
        forecasts = self.load_forecasts(
            config,
            week_start,
            range_end,
            tz,
            force=force,
        )
        local_today = today or datetime.now(tz).date()
        history_end = min(local_today, range_end)
        if historical_weather_enabled() and config.latitude and config.longitude and week_start < history_end:
            forecasts.update(self.load_historical_forecasts(config, week_start, history_end, tz, force=force))
        return weather_days_from_daily_forecasts(forecasts, week_start)

    def load_forecasts(
        self,
        config: WeatherConfig,
        range_start: date,
        range_end: date,
        tz: ZoneInfo,
        *,
        force: bool = False,
    ) -> dict[date, DailyForecast]:
        return self._load_forecasts_from_source(
            config,
            config.provider,
            range_start,
            range_end,
            tz,
            force=force,
        )

    def load_historical_forecasts(
        self,
        config: WeatherConfig,
        range_start: date,
        range_end: date,
        tz: ZoneInfo,
        *,
        force: bool = False,
    ) -> dict[date, DailyForecast]:
        if range_end <= range_start:
            return {}
        return self._load_forecasts_from_source(
            config,
            "open-meteo-history",
            range_start,
            range_end,
            tz,
            force=force,
        )

    def _load_forecasts_from_source(
        self,
        config: WeatherConfig,
        source: str,
        range_start: date,
        range_end: date,
        tz: ZoneInfo,
        *,
        force: bool = False,
    ) -> dict[date, DailyForecast]:
        key = WeatherForecastKey(config, source, range_start, range_end, timezone_key(tz))
        now = time_module.time()
        with self._lock:
            entry = self._forecasts.get(key)
            if not force and entry is not None and entry.expires_at > now:
                return dict(entry.forecasts)

        if source == "open-meteo-history":
            payload = fetch_json(open_meteo_archive_url(config, tz, range_start, range_end), config, source)
            forecasts = open_meteo_daily_forecasts_from_payload(payload)
        elif source == "open-meteo":
            payload = fetch_json(open_meteo_forecast_url(config, tz, range_start, range_end), config)
            forecasts = open_meteo_daily_forecasts_from_payload(payload)
        else:
            forecast_url = self.forecast_url(config, force=force)
            payload = fetch_json(forecast_url, config)
            forecasts = daily_forecasts_from_payload(payload, tz)

        filtered = {day: forecast for day, forecast in forecasts.items() if range_start <= day < range_end}

        with self._lock:
            self._forecasts[key] = WeatherForecastCacheEntry(now + config.ttl_seconds, dict(filtered))
        return filtered

    def forecast_url(self, config: WeatherConfig, *, force: bool = False) -> str:
        if config.forecast_url:
            return config.forecast_url

        key = PointKey(config.api_base, config.latitude, config.longitude, config.user_agent)
        now = time_module.time()
        with self._lock:
            entry = self._points.get(key)
            if not force and entry is not None and entry.expires_at > now:
                return entry.forecast_url

        point_url = f"{config.api_base}/points/{config.latitude},{config.longitude}"
        payload = fetch_json(point_url, config)
        forecast_url = point_forecast_url(payload)

        with self._lock:
            self._points[key] = PointCacheEntry(now + config.point_ttl_seconds, forecast_url)
        return forecast_url

    def clear(self) -> None:
        with self._lock:
            self._forecasts.clear()
            self._points.clear()


_WEATHER_CACHE = WeatherCache()


def load_weekly_weather(
    week_start: date,
    *,
    force: bool = False,
    tz: ZoneInfo | None = None,
    today: date | None = None,
) -> tuple[WeatherDays | None, str]:
    config = configured_weather()
    if config is None:
        return None, "mock-weather"

    try:
        days = _WEATHER_CACHE.load_days(config, week_start, tz or local_zone(), force=force, today=today)
    except Exception as exc:
        return None, f"{config.provider}-error:{exc.__class__.__name__}"
    return days, config.provider


def load_weather_forecasts(
    range_start: date,
    range_end: date,
    *,
    force: bool = False,
    tz: ZoneInfo | None = None,
) -> tuple[dict[date, DailyForecast] | None, str]:
    config = configured_weather()
    if config is None:
        return None, "mock-weather"

    try:
        forecasts = _WEATHER_CACHE.load_forecasts(config, range_start, range_end, tz or local_zone(), force=force)
    except Exception as exc:
        return None, f"{config.provider}-error:{exc.__class__.__name__}"
    return forecasts, config.provider


def clear_weather_cache() -> None:
    _WEATHER_CACHE.clear()


def configured_weather() -> WeatherConfig | None:
    enabled = os.environ.get("TRMNL_WEATHER_ENABLED", "1").strip().lower()
    if enabled in {"0", "false", "no", "off"}:
        return None

    provider = normalize_weather_provider(os.environ.get("TRMNL_WEATHER_PROVIDER", "nws"))
    forecast_url = os.environ.get("TRMNL_WEATHER_FORECAST_URL", "").strip()
    latitude = os.environ.get("TRMNL_WEATHER_LAT", "").strip()
    longitude = os.environ.get("TRMNL_WEATHER_LON", "").strip()
    if provider == "open-meteo" and not (latitude and longitude):
        return None
    if provider == "nws" and not forecast_url and not (latitude and longitude):
        return None

    return WeatherConfig(
        provider=provider,
        latitude=latitude,
        longitude=longitude,
        forecast_url=forecast_url,
        api_base=os.environ.get("TRMNL_WEATHER_API_BASE", DEFAULT_NWS_API_BASE).strip().rstrip("/"),
        open_meteo_api_url=os.environ.get("TRMNL_OPEN_METEO_API_URL", DEFAULT_OPEN_METEO_API_URL).strip(),
        open_meteo_archive_api_url=os.environ.get(
            "TRMNL_OPEN_METEO_ARCHIVE_API_URL",
            DEFAULT_OPEN_METEO_ARCHIVE_API_URL,
        ).strip(),
        user_agent=os.environ.get("TRMNL_WEATHER_USER_AGENT", DEFAULT_NWS_USER_AGENT).strip(),
        timeout_seconds=env_float("TRMNL_WEATHER_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
        ttl_seconds=env_int("TRMNL_WEATHER_TTL_SECONDS", DEFAULT_WEATHER_TTL_SECONDS),
        point_ttl_seconds=env_int("TRMNL_WEATHER_POINT_TTL_SECONDS", DEFAULT_POINT_TTL_SECONDS),
    )


def normalize_weather_provider(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if normalized in {"open-meteo", "openmeteo"}:
        return "open-meteo"
    return "nws"


def historical_weather_enabled() -> bool:
    value = os.environ.get("TRMNL_WEEKLY_HISTORICAL_WEATHER", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return max(0.1, float(os.environ.get(name, str(default))))
    except ValueError:
        return default


def fetch_json(url: str, config: WeatherConfig, provider: str | None = None) -> Any:
    request = Request(
        url,
        headers={
            "User-Agent": config.user_agent,
            "Accept": "application/geo+json",
        },
    )
    started_at = perf_counter()
    try:
        with urlopen(request, timeout=config.timeout_seconds) as response:
            body = response.read()
            log_http_call(
                provider=provider or config.provider,
                url=url,
                status=getattr(response, "status", response.getcode()),
                duration_ms=duration_ms(started_at),
                bytes_read=len(body),
            )
            return json.loads(body.decode("utf-8"))
    except HTTPError as exc:
        log_http_call(
            provider=provider or config.provider,
            url=url,
            status=exc.code,
            duration_ms=duration_ms(started_at),
            error=exc.__class__.__name__,
        )
        raise
    except Exception as exc:
        log_http_call(
            provider=provider or config.provider,
            url=url,
            status="error",
            duration_ms=duration_ms(started_at),
            error=exc.__class__.__name__,
        )
        raise


def point_forecast_url(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise ValueError("NWS point response was not an object")
    properties = payload.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("NWS point response did not include properties")
    forecast_url = properties.get("forecast")
    if not isinstance(forecast_url, str) or not forecast_url:
        raise ValueError("NWS point response did not include a forecast URL")
    return forecast_url


def days_from_nws_forecast(payload: Any, week_start: date, tz: ZoneInfo) -> WeatherDays:
    daily = daily_forecasts_from_payload(payload, tz)
    return weather_days_from_daily_forecasts(daily, week_start)


def days_from_open_meteo_forecast(payload: Any, week_start: date) -> WeatherDays:
    daily = open_meteo_daily_forecasts_from_payload(payload)
    return weather_days_from_daily_forecasts(daily, week_start)


def weather_days_from_daily_forecasts(daily: dict[date, DailyForecast], week_start: date) -> WeatherDays:
    days: WeatherDays = []
    for offset in range(WEEKLY_DAY_COUNT):
        day = week_start + timedelta(days=offset)
        forecast = daily.get(day)
        if forecast is not None and forecast.has_temperature():
            kind = forecast.icon_kind()
            temp = format_daily_temperature(forecast)
        else:
            kind = "cloud"
            temp = MISSING_TEMP_LABEL
        days.append((day.strftime("%a").upper(), str(day.day), kind, temp))
    return days


def daily_forecasts_from_payload(payload: Any, tz: ZoneInfo) -> dict[date, DailyForecast]:
    if not isinstance(payload, dict):
        raise ValueError("NWS forecast response was not an object")
    properties = payload.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("NWS forecast response did not include properties")
    periods = properties.get("periods")
    if not isinstance(periods, list):
        raise ValueError("NWS forecast response did not include periods")

    daily: dict[date, DailyForecast] = {}
    for period in periods:
        if not isinstance(period, dict):
            continue
        start_time = period.get("startTime")
        if not isinstance(start_time, str):
            continue

        day = parse_nws_datetime(start_time, tz).date()
        forecast = daily.setdefault(day, DailyForecast())
        temperature = parse_temperature(period.get("temperature"), period.get("temperatureUnit"))
        icon = icon_for_forecast(str(period.get("shortForecast") or period.get("name") or ""))

        if bool(period.get("isDaytime")):
            if temperature is not None:
                forecast.high = temperature if forecast.high is None else max(forecast.high, temperature)
            forecast.day_icon = icon
        else:
            if temperature is not None:
                forecast.low = temperature if forecast.low is None else min(forecast.low, temperature)
            forecast.night_icon = icon
    return daily


def open_meteo_daily_forecasts_from_payload(payload: Any) -> dict[date, DailyForecast]:
    if not isinstance(payload, dict):
        raise ValueError("Open-Meteo forecast response was not an object")
    daily = payload.get("daily")
    if not isinstance(daily, dict):
        raise ValueError("Open-Meteo forecast response did not include daily data")

    times = daily.get("time")
    highs = daily.get("temperature_2m_max")
    lows = daily.get("temperature_2m_min")
    codes = daily.get("weather_code")
    if not isinstance(times, list):
        raise ValueError("Open-Meteo forecast response did not include daily times")

    forecasts: dict[date, DailyForecast] = {}
    for index, value in enumerate(times):
        if not isinstance(value, str):
            continue
        day = date.fromisoformat(value)
        high = parse_temperature(list_value(highs, index), "F")
        low = parse_temperature(list_value(lows, index), "F")
        icon = icon_for_wmo_code(list_value(codes, index))
        forecasts[day] = DailyForecast(high=high, low=low, day_icon=icon)
    return forecasts


def open_meteo_forecast_url(
    config: WeatherConfig,
    tz: ZoneInfo,
    range_start: date | None = None,
    range_end: date | None = None,
) -> str:
    params = {
        "latitude": config.latitude,
        "longitude": config.longitude,
        "daily": "weather_code,temperature_2m_max,temperature_2m_min",
        "temperature_unit": "fahrenheit",
        "timezone": timezone_key(tz),
    }
    if range_start is not None and range_end is not None:
        params["start_date"] = range_start.isoformat()
        params["end_date"] = (range_end - timedelta(days=1)).isoformat()
    else:
        params["forecast_days"] = "16"
    return f"{config.open_meteo_api_url}?{urlencode(params)}"


def open_meteo_archive_url(
    config: WeatherConfig,
    tz: ZoneInfo,
    range_start: date,
    range_end: date,
) -> str:
    params = {
        "latitude": config.latitude,
        "longitude": config.longitude,
        "daily": "weather_code,temperature_2m_max,temperature_2m_min",
        "temperature_unit": "fahrenheit",
        "timezone": timezone_key(tz),
        "start_date": range_start.isoformat(),
        "end_date": (range_end - timedelta(days=1)).isoformat(),
    }
    return f"{config.open_meteo_archive_api_url}?{urlencode(params)}"


def list_value(values: Any, index: int) -> Any:
    if not isinstance(values, list) or index >= len(values):
        return None
    return values[index]


def parse_nws_datetime(value: str, tz: ZoneInfo) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def parse_temperature(value: Any, unit: Any) -> int | None:
    if value is None:
        return None
    try:
        temperature = float(value)
    except (TypeError, ValueError):
        return None

    if str(unit).upper() == "C":
        temperature = temperature * 9 / 5 + 32
    return round(temperature)


def format_daily_temperature(forecast: DailyForecast) -> str:
    high = "--" if forecast.high is None else str(forecast.high)
    low = "--" if forecast.low is None else str(forecast.low)
    return f"{high} / {low}"


def icon_for_forecast(text: str) -> str:
    normalized = text.lower().replace("-", " ")
    if contains_any(normalized, ("thunder", "t storm", "tstorm", "lightning")):
        return "storm"
    if contains_any(normalized, ("snow", "flurries", "sleet", "freezing rain", "wintry")):
        return "snow"
    if contains_any(normalized, ("rain", "shower", "drizzle", "downpour")):
        return "rain"
    if contains_any(normalized, ("fog", "smoke", "haze")):
        return "fog"
    if contains_any(normalized, ("wind", "breezy", "gust")):
        return "wind"
    if contains_any(normalized, ("overcast", "cloud")):
        return "cloud"
    if contains_any(normalized, ("partly", "mostly sunny", "few clouds")):
        return "partly"
    if contains_any(normalized, ("sunny", "clear")):
        return "clear"
    return "cloud"


def icon_for_wmo_code(value: Any) -> str:
    try:
        code = int(value)
    except (TypeError, ValueError):
        return "cloud"

    if code == 0:
        return "clear"
    if code in {1, 2}:
        return "partly"
    if code == 3:
        return "cloud"
    if code in {45, 48}:
        return "fog"
    if code in {71, 73, 75, 77, 85, 86}:
        return "snow"
    if code in {95, 96, 99}:
        return "storm"
    if code in {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}:
        return "rain"
    return "cloud"


def contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def local_zone() -> ZoneInfo:
    return ZoneInfo(os.environ.get("TRMNL_TIMEZONE", "America/Denver"))


def timezone_key(tz: ZoneInfo) -> str:
    return getattr(tz, "key", str(tz))
