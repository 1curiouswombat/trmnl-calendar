from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from threading import RLock, Thread
from typing import Callable
from urllib.parse import parse_qs, urlparse

from PIL import Image

from trmnl_weekly_calendar.calendar_data import load_events, load_month_events
from trmnl_weekly_calendar.month_render import MONTH_WEEK_ROWS, render_month_image
from trmnl_weekly_calendar.png_encode import encode_png_grayscale_4bit
from trmnl_weekly_calendar.render import OUT, configured_now, days_for_week, render_image, start_of_week
from trmnl_weekly_calendar.weather_data import (
    format_daily_temperature,
    load_weather_forecasts,
    load_weekly_weather,
)


DEFAULT_REFRESH_SECONDS = 15 * 60
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_RENDER_CACHE_DIR = OUT / "server-cache"
DEFAULT_RENDER_PREWARM_DELAY_SECONDS = 5.0
LOGGER = logging.getLogger("trmnl_weekly_calendar.server")


@dataclass
class CalendarPlugin:
    name: str
    filename_prefix: str
    render: Callable[[datetime, bool], tuple[Image.Image, str]]


@dataclass
class RenderedCalendar:
    bucket: int
    body: bytes
    fingerprint: str
    source: str
    generated_at: datetime


class CalendarCache:
    def __init__(self, plugin: CalendarPlugin, refresh_seconds: int, cache_dir: Path | None = None) -> None:
        self.plugin = plugin
        self.refresh_seconds = refresh_seconds
        self.cache_dir = cache_dir or render_cache_dir()
        self._persist_render_cache = persistent_render_cache_enabled()
        self._lock = RLock()
        self._rendered: RenderedCalendar | None = self._load_persisted()
        self._refreshing = False

    def get(self, *, force: bool = False) -> RenderedCalendar:
        bucket = int(time.time() // self.refresh_seconds)
        with self._lock:
            if not force and self._rendered and self._rendered.bucket == bucket:
                return self._rendered
            if not force and self._rendered:
                self._start_refresh_locked(bucket, force=False)
                return self._rendered

        return self._render(bucket, force=force)

    def prewarm(self) -> None:
        bucket = int(time.time() // self.refresh_seconds)
        with self._lock:
            if self._rendered and self._rendered.bucket == bucket:
                return
            self._start_refresh_locked(bucket, force=False)

    def ensure_initial(self) -> None:
        with self._lock:
            if self._rendered is not None:
                return
        self._render(int(time.time() // self.refresh_seconds), force=False)

    def _start_refresh_locked(self, bucket: int, *, force: bool) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        thread = Thread(target=self._refresh_background, args=(bucket, force), daemon=True)
        thread.start()

    def _refresh_background(self, bucket: int, force: bool) -> None:
        try:
            self._render(bucket, force=force)
        except Exception:
            LOGGER.exception("Background render failed for %s", self.plugin.name)
            with self._lock:
                self._refreshing = False

    def _render(self, bucket: int, *, force: bool) -> RenderedCalendar:
        generated_at = configured_now()
        image, source = self.plugin.render(generated_at, force)
        body = encode_image(image)
        digest = hashlib.sha256(body).hexdigest()[:16]
        rendered = RenderedCalendar(bucket, body, digest, source, generated_at)
        with self._lock:
            self._rendered = rendered
            self._refreshing = False
        self._persist(rendered)
        return rendered

    def _load_persisted(self) -> RenderedCalendar | None:
        if not self._persist_render_cache:
            return None
        meta_path, body_path = self._cache_paths()
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            body = body_path.read_bytes()
            fingerprint = str(metadata.get("fingerprint") or hashlib.sha256(body).hexdigest()[:16])
            return RenderedCalendar(
                bucket=int(metadata.get("bucket", -1)),
                body=body,
                fingerprint=fingerprint,
                source=str(metadata.get("source", "persisted")),
                generated_at=datetime.fromisoformat(str(metadata["generated_at"])),
            )
        except FileNotFoundError:
            return None
        except Exception:
            LOGGER.exception("Failed to load persisted render cache for %s", self.plugin.name)
            return None

    def _persist(self, rendered: RenderedCalendar) -> None:
        if not self._persist_render_cache:
            return
        meta_path, body_path = self._cache_paths()
        metadata = {
            "bucket": rendered.bucket,
            "fingerprint": rendered.fingerprint,
            "source": rendered.source,
            "generated_at": rendered.generated_at.isoformat(),
        }
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            body_path.write_bytes(rendered.body)
            meta_path.write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
        except Exception:
            LOGGER.exception("Failed to persist render cache for %s", self.plugin.name)

    def _cache_paths(self) -> tuple[Path, Path]:
        return (
            self.cache_dir / f"{self.plugin.name}.json",
            self.cache_dir / f"{self.plugin.name}.png",
        )


def encode_image(image) -> bytes:
    mode = os.environ.get("TRMNL_IMAGE_MODE", "4bit").lower()
    if mode in {"4bit", "4-bit", "gray4", "grayscale4"}:
        return encode_png_grayscale_4bit(image)

    if mode in {"1bit", "bw", "black-white"}:
        image = image.convert("1")
    elif mode in {"gray", "greyscale", "grayscale"}:
        pass
    else:
        return encode_png_grayscale_4bit(image)

    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def render_weekly(generated_at: datetime, force: bool = False) -> tuple[Image.Image, str]:
    view_start = generated_at.date()
    events, all_day_events, source = load_events(view_start, force=force)
    weather_days, weather_source = load_weekly_weather(view_start, force=force, today=generated_at.date())
    image = render_image(
        week_start=view_start,
        days=weather_days or days_for_week(view_start),
        events=events,
        all_day_events=all_day_events,
        now=generated_at,
    )
    return image, f"{source}+{weather_source}"


def render_month(generated_at: datetime, force: bool = False) -> tuple[Image.Image, str]:
    visible_start = start_of_week(generated_at.date())
    visible_end = visible_start + timedelta(weeks=MONTH_WEEK_ROWS)
    events, source = load_month_events(visible_start, range_end=visible_end, force=force)
    weather_start = generated_at.date()
    weather_end = weather_start + timedelta(days=7)
    forecasts, weather_source = load_weather_forecasts(weather_start, weather_end, force=force)
    weather = {}
    if forecasts is not None:
        weather = {
            day: (forecast.icon_kind(), format_daily_temperature(forecast))
            for day, forecast in forecasts.items()
            if forecast.has_temperature()
        }
    image = render_month_image(view_start=visible_start, events=events, weather=weather, now=generated_at)
    return image, f"{source}+{weather_source}"


def plugin_specs() -> dict[str, CalendarPlugin]:
    return {
        "weekly": CalendarPlugin("weekly", "weekly-calendar", render_weekly),
        "month": CalendarPlugin("month", "month-calendar", render_month),
    }


def make_handler(caches: dict[str, CalendarCache]):
    class TRMNLCalendarHandler(BaseHTTPRequestHandler):
        server_version = "TRMNLWeeklyCalendar/0.1"

        def do_GET(self) -> None:
            self.handle_request(send_body=True)

        def do_HEAD(self) -> None:
            self.handle_request(send_body=False)

        def handle_request(self, *, send_body: bool) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            force_refresh = refresh_requested(parsed.query)
            try:
                if path in {"/", "/healthz"}:
                    self.send_text("ok\n", send_body=send_body)
                    return

                route = resolve_route(path)
                if route is None:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return

                cache = caches[route.plugin_name]
                rendered = cache.get(force=force_refresh)
                if route.kind == "json":
                    self.send_redirect_json(rendered, cache, route.image_path, send_body=send_body)
                else:
                    self.send_png(rendered.body, cache, send_body=send_body, no_store=force_refresh)
            except Exception as exc:
                self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

        def send_redirect_json(
            self,
            rendered: RenderedCalendar,
            cache: CalendarCache,
            image_path: str,
            *,
            send_body: bool,
        ) -> None:
            image_url = f"{self.base_url()}{image_path}?v={rendered.fingerprint}"
            payload = {
                "filename": f"{cache.plugin.filename_prefix}-{rendered.fingerprint}",
                "url": image_url,
                "refresh_rate": cache.refresh_seconds,
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if send_body:
                self.wfile.write(body)

        def send_png(self, body: bytes, cache: CalendarCache, *, send_body: bool, no_store: bool = False) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            if no_store:
                self.send_header("Cache-Control", "no-store")
            else:
                self.send_header("Cache-Control", f"public, max-age={cache.refresh_seconds}")
            self.end_headers()
            if send_body:
                self.wfile.write(body)

        def send_text(self, text: str, *, send_body: bool) -> None:
            body = text.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if send_body:
                self.wfile.write(body)

        def base_url(self) -> str:
            configured = os.environ.get("TRMNL_PUBLIC_BASE_URL", "").strip().rstrip("/")
            if configured:
                return configured
            proto = self.headers.get("X-Forwarded-Proto", "http")
            host = self.headers.get("Host", f"localhost:{self.server.server_port}")
            return f"{proto}://{host}"

        def log_message(self, format: str, *args) -> None:
            if os.environ.get("TRMNL_QUIET"):
                return
            super().log_message(format, *args)

    return TRMNLCalendarHandler


@dataclass(frozen=True)
class Route:
    kind: str
    plugin_name: str
    image_path: str


def resolve_route(path: str) -> Route | None:
    if path == "/trmnl.json":
        return Route("json", "weekly", "/image.png")
    if path in {"/image.png", "/calendar.png"}:
        return Route("image", "weekly", path)

    parts = [part for part in path.split("/") if part]
    if len(parts) != 2 or parts[0] not in {"weekly", "month"}:
        return None

    plugin_name, leaf = parts
    image_path = f"/{plugin_name}/image.png"
    if leaf == "trmnl.json":
        return Route("json", plugin_name, image_path)
    if leaf in {"image.png", "calendar.png"}:
        return Route("image", plugin_name, image_path)
    return None


def refresh_requested(query: str) -> bool:
    params = parse_qs(query, keep_blank_values=True)
    for key in ("refresh", "force", "regen"):
        if key not in params:
            continue
        values = [value.lower() for value in params[key]]
        return not any(value in {"0", "false", "no"} for value in values)
    return False


def configure_logging() -> None:
    level_name = os.environ.get("TRMNL_LOG_LEVEL", DEFAULT_LOG_LEVEL).strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def render_cache_dir() -> Path:
    configured = os.environ.get("TRMNL_RENDER_CACHE_DIR", "").strip()
    if configured:
        return Path(configured)
    return DEFAULT_RENDER_CACHE_DIR


def persistent_render_cache_enabled() -> bool:
    value = os.environ.get("TRMNL_PERSIST_RENDER_CACHE", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def render_prewarm_delay_seconds() -> float:
    value = os.environ.get("TRMNL_RENDER_PREWARM_DELAY_SECONDS", str(DEFAULT_RENDER_PREWARM_DELAY_SECONDS)).strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        return DEFAULT_RENDER_PREWARM_DELAY_SECONDS


def seconds_until_next_prewarm(now: float, refresh_seconds: int, delay_seconds: float) -> float:
    bucket = int(now // refresh_seconds) * refresh_seconds
    prewarm_at = bucket + delay_seconds
    if prewarm_at <= now:
        prewarm_at = bucket + refresh_seconds + delay_seconds
    return max(1.0, prewarm_at - now)


def start_prewarm_loop(caches: dict[str, CalendarCache], refresh_seconds: int) -> Thread:
    delay_seconds = render_prewarm_delay_seconds()

    def loop() -> None:
        while True:
            time.sleep(seconds_until_next_prewarm(time.time(), refresh_seconds, delay_seconds))
            for cache in caches.values():
                cache.prewarm()

    thread = Thread(target=loop, name="render-prewarm", daemon=True)
    thread.start()
    LOGGER.info(
        "Render cache prewarm loop started refresh_seconds=%s delay_seconds=%.1f",
        refresh_seconds,
        delay_seconds,
    )
    return thread


def main() -> None:
    configure_logging()
    host = os.environ.get("TRMNL_HOST", "0.0.0.0")
    port = int(os.environ.get("TRMNL_PORT", "8787"))
    refresh_seconds = int(os.environ.get("TRMNL_REFRESH_SECONDS", str(DEFAULT_REFRESH_SECONDS)))
    plugins = plugin_specs()
    caches = {name: CalendarCache(plugin, refresh_seconds) for name, plugin in plugins.items()}
    for cache in caches.values():
        cache.ensure_initial()
    server = ThreadingHTTPServer((host, port), make_handler(caches))
    for cache in caches.values():
        cache.prewarm()
    start_prewarm_loop(caches, refresh_seconds)
    print(f"Serving TRMNL calendar plugins on http://{host}:{port}")
    print(f"Weekly Redirect JSON: http://{host}:{port}/weekly/trmnl.json")
    print(f"Month Redirect JSON:  http://{host}:{port}/month/trmnl.json")
    print(f"Legacy weekly JSON:   http://{host}:{port}/trmnl.json")
    server.serve_forever()


if __name__ == "__main__":
    main()
