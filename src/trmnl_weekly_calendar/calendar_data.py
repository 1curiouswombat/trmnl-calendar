from __future__ import annotations

import json
import os
import shlex
import subprocess
import time as time_module
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from threading import RLock
from typing import Any
from zoneinfo import ZoneInfo

from trmnl_weekly_calendar.constants import WEEKLY_DAY_COUNT
from trmnl_weekly_calendar.external_api_log import (
    duration_ms,
    log_subprocess_call,
    perf_counter,
    stable_fingerprint,
)
from trmnl_weekly_calendar.render import (
    DAY_END_HOUR,
    DAY_START_HOUR,
    AllDayEvent,
    Event,
    MOCK_ALL_DAY_EVENTS,
    MOCK_EVENTS,
    MOCK_WEEK_START,
)


TONE_STEPS = [238, 232, 240, 235, 242, 228, 244, 229, 226, 241, 234]
CALENDAR_TONES = {
    "peter st. john": 232,
    "primary": 232,
    "corbin": 238,
    "family": 226,
}
DEFAULT_CALENDAR_DATA_TTL_SECONDS = 2 * 60 * 60


@dataclass(frozen=True)
class CalendarDataKey:
    command_template: str
    account: str
    calendar_sources: tuple[str, ...]
    timezone: str


@dataclass(frozen=True)
class CalendarSource:
    label: str
    calendar_id: str


@dataclass
class RawEventCacheEntry:
    key: CalendarDataKey
    range_start: date
    range_end: date
    expires_at: float
    raw_events: list[dict[str, Any]]


@dataclass(frozen=True)
class MonthEvent:
    day: date
    title: str
    time_label: str = ""
    tone: int = 238
    sort_minutes: int = 0


class CalendarDataCache:
    def __init__(self) -> None:
        self._lock = RLock()
        self._entries: list[RawEventCacheEntry] = []

    def load_raw_events(
        self,
        command_template: str,
        range_start: date,
        range_end: date,
        tz: ZoneInfo,
        *,
        force: bool = False,
    ) -> list[dict[str, Any]]:
        key = calendar_data_key(command_template, tz)
        now = time_module.time()
        with self._lock:
            self._prune(now)
            if not force:
                cached = self._find_superset(key, range_start, range_end)
                if cached is not None:
                    return filter_raw_events(cached.raw_events, range_start, range_end, tz)

            raw_events = load_gog_events(command_template, range_start, range_end)
            self._entries.append(
                RawEventCacheEntry(
                    key=key,
                    range_start=range_start,
                    range_end=range_end,
                    expires_at=now + calendar_data_ttl_seconds(),
                    raw_events=raw_events,
                )
            )
            return filter_raw_events(raw_events, range_start, range_end, tz)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def _prune(self, now: float) -> None:
        self._entries = [entry for entry in self._entries if entry.expires_at > now]

    def _find_superset(
        self,
        key: CalendarDataKey,
        range_start: date,
        range_end: date,
    ) -> RawEventCacheEntry | None:
        eligible: list[RawEventCacheEntry] = []
        for entry in self._entries:
            if entry.key != key:
                continue
            if entry.range_start <= range_start and range_end <= entry.range_end:
                eligible.append(entry)
        if not eligible:
            return None
        return max(eligible, key=lambda item: item.expires_at)


_CALENDAR_DATA_CACHE = CalendarDataCache()


def load_events(week_start: date, *, force: bool = False) -> tuple[list[Event], list[AllDayEvent], str]:
    command_template = os.environ.get("TRMNL_GOG_COMMAND", "").strip()
    if not command_template:
        return MOCK_EVENTS, MOCK_ALL_DAY_EVENTS, "mock"

    tz = local_zone()
    raw_events = _CALENDAR_DATA_CACHE.load_raw_events(
        command_template,
        week_start,
        week_start + timedelta(days=WEEKLY_DAY_COUNT),
        tz,
        force=force,
    )
    events, all_day_events = parse_events(raw_events, week_start, tz)
    return events, all_day_events, "gog"


def load_month_events(
    range_start: date,
    *,
    range_end: date | None = None,
    force: bool = False,
) -> tuple[list[MonthEvent], str]:
    range_stop = range_end or next_month(range_start)
    command_template = os.environ.get("TRMNL_GOG_COMMAND", "").strip()
    if not command_template:
        return mock_month_events(range_start, range_end=range_stop), "mock"

    tz = local_zone()
    raw_events = _CALENDAR_DATA_CACHE.load_raw_events(
        command_template,
        range_start,
        range_stop,
        tz,
        force=force,
    )
    return parse_month_events(raw_events, range_start, range_stop, tz), "gog"


def clear_calendar_data_cache() -> None:
    _CALENDAR_DATA_CACHE.clear()


def calendar_data_ttl_seconds() -> int:
    return int(
        os.environ.get(
            "TRMNL_CALENDAR_DATA_TTL_SECONDS",
            str(DEFAULT_CALENDAR_DATA_TTL_SECONDS),
        )
    )


def calendar_data_key(command_template: str, tz: ZoneInfo) -> CalendarDataKey:
    account = os.environ.get("GOG_ACCOUNT", "").strip()
    return CalendarDataKey(
        command_template=command_template,
        account=account,
        calendar_sources=tuple(calendar_source_key(source) for source in configured_calendar_sources(account)),
        timezone=getattr(tz, "key", str(tz)),
    )


def load_gog_events(command_template: str, range_start: date, range_end: date) -> list[dict[str, Any]]:
    account = os.environ.get("GOG_ACCOUNT", "").strip()
    sources = configured_calendar_sources(account)
    if not sources:
        return extract_event_list(run_gog(command_template, range_start, range_end, None))
    if "{calendar}" not in command_template:
        raise RuntimeError("TRMNL_GOG_CALENDARS requires TRMNL_GOG_COMMAND to include {calendar}")

    raw_events: list[dict[str, Any]] = []
    for source in sources:
        payload = run_gog(command_template, range_start, range_end, source.calendar_id)
        for event in extract_event_list(payload):
            tagged = dict(event)
            tagged["_trmnl_calendar_label"] = source.label
            tagged["_trmnl_calendar_id"] = source.calendar_id
            raw_events.append(tagged)
    return raw_events


def configured_calendar_sources(account: str) -> list[CalendarSource]:
    value = os.environ.get("TRMNL_GOG_CALENDARS", "").strip()
    if not value:
        return []

    sources: list[CalendarSource] = []
    for part in value.split(","):
        spec = part.strip()
        if not spec:
            continue
        if "=" in spec:
            label, calendar_id = spec.split("=", 1)
        else:
            label = spec
            calendar_id = spec
        label = label.strip()
        calendar_id = calendar_id.strip()
        if calendar_id == "{account}":
            calendar_id = account
        if label and calendar_id:
            sources.append(CalendarSource(label, calendar_id))
    return sources


def calendar_source_key(source: CalendarSource) -> str:
    return f"{source.label}={source.calendar_id}"


def run_gog(command_template: str, range_start: date, range_end: date, calendar_id: str | None) -> Any:
    account = os.environ.get("GOG_ACCOUNT", "").strip()
    if "{account}" in command_template and not account:
        raise RuntimeError("TRMNL_GOG_COMMAND uses {account}, but GOG_ACCOUNT is not set")
    if "{calendar}" in command_template and not calendar_id:
        raise RuntimeError("TRMNL_GOG_COMMAND uses {calendar}, but no calendar was configured")

    values = {
        "account": account,
        "calendar": calendar_id or "",
        "start": range_start.isoformat(),
        "end": range_end.isoformat(),
        "start_datetime": datetime.combine(range_start, time.min).isoformat(),
        "end_datetime": datetime.combine(range_end, time.min).isoformat(),
    }
    command = shlex.split(command_template.format(**values))
    started_at = perf_counter()
    log_fields = {
        "range_start": range_start.isoformat(),
        "range_end": range_end.isoformat(),
        "calendar": stable_fingerprint(calendar_id),
    }
    try:
        result = subprocess.run(command, capture_output=True, check=True, text=True, timeout=30)
    except subprocess.CalledProcessError as exc:
        log_subprocess_call(
            provider="gog",
            command=command,
            operation="calendar-events",
            status=exc.returncode,
            duration_ms=duration_ms(started_at),
            error=exc.__class__.__name__,
            **log_fields,
        )
        raise
    except subprocess.TimeoutExpired as exc:
        log_subprocess_call(
            provider="gog",
            command=command,
            operation="calendar-events",
            status="timeout",
            duration_ms=duration_ms(started_at),
            error=exc.__class__.__name__,
            **log_fields,
        )
        raise
    except Exception as exc:
        log_subprocess_call(
            provider="gog",
            command=command,
            operation="calendar-events",
            status="error",
            duration_ms=duration_ms(started_at),
            error=exc.__class__.__name__,
            **log_fields,
        )
        raise

    log_subprocess_call(
        provider="gog",
        command=command,
        operation="calendar-events",
        status=result.returncode,
        duration_ms=duration_ms(started_at),
        stdout_bytes=len(result.stdout),
        **log_fields,
    )
    return json.loads(result.stdout)


def extract_event_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        return []

    for key in ("items", "events", "data", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    flattened: list[dict[str, Any]] = []
    for value in payload.values():
        if isinstance(value, list):
            flattened.extend(item for item in value if isinstance(item, dict) and has_event_time(item))
    return flattened


def filter_raw_events(
    raw_events: list[dict[str, Any]],
    range_start: date,
    range_end: date,
    tz: ZoneInfo,
) -> list[dict[str, Any]]:
    return [event for event in raw_events if raw_event_overlaps_range(event, range_start, range_end, tz)]


def raw_event_overlaps_range(raw: dict[str, Any], range_start: date, range_end: date, tz: ZoneInfo) -> bool:
    start_value = first_value(raw, "start", "startTime", "start_time", "starts_at", "begin")
    end_value = first_value(raw, "end", "endTime", "end_time", "ends_at", "finish")
    if start_value is None:
        return False

    try:
        start, start_is_all_day = parse_temporal(start_value, tz)
        end, end_is_all_day = parse_temporal(end_value, tz) if end_value is not None else (start, start_is_all_day)
    except ValueError:
        return True

    if start_is_all_day or end_is_all_day:
        start_day = start if isinstance(start, date) and not isinstance(start, datetime) else start.date()
        end_day = end if isinstance(end, date) and not isinstance(end, datetime) else end.date()
        exclusive_end = end_day if end_day > start_day else start_day + timedelta(days=1)
        return start_day < range_end and exclusive_end > range_start

    start_dt = ensure_datetime(start, tz)
    end_dt = ensure_datetime(end, tz)
    if end_dt <= start_dt:
        end_dt = start_dt + timedelta(minutes=30)
    range_start_dt = datetime.combine(range_start, time.min, tz)
    range_end_dt = datetime.combine(range_end, time.min, tz)
    return start_dt < range_end_dt and end_dt > range_start_dt


def parse_events(
    raw_events: list[dict[str, Any]],
    week_start: date,
    tz: ZoneInfo,
) -> tuple[list[Event], list[AllDayEvent]]:
    timed: list[Event] = []
    all_day: list[AllDayEvent] = []
    week_end = week_start + timedelta(days=WEEKLY_DAY_COUNT)

    for index, raw in enumerate(raw_events):
        title = clean_text(raw.get("summary") or raw.get("title") or raw.get("name") or "Untitled")
        location = clean_text(raw.get("location") or raw.get("where") or "")
        start_value = first_value(raw, "start", "startTime", "start_time", "starts_at", "begin")
        end_value = first_value(raw, "end", "endTime", "end_time", "ends_at", "finish")
        if start_value is None:
            continue

        start, start_is_all_day = parse_temporal(start_value, tz)
        end, end_is_all_day = parse_temporal(end_value, tz) if end_value is not None else (start, start_is_all_day)
        tone = tone_for_raw_event(raw, index)

        if start_is_all_day or end_is_all_day:
            start_day = start if isinstance(start, date) and not isinstance(start, datetime) else start.date()
            end_day = end if isinstance(end, date) and not isinstance(end, datetime) else end.date()
            inclusive_end = end_day - timedelta(days=1) if end_day > start_day else start_day
            clipped_start = max(start_day, week_start)
            clipped_end = min(inclusive_end, week_end - timedelta(days=1))
            if clipped_start <= clipped_end:
                all_day.append(
                    AllDayEvent(
                        (clipped_start - week_start).days,
                        (clipped_end - week_start).days,
                        title,
                        tone=tone,
                    )
                )
            continue

        start_dt = ensure_datetime(start, tz)
        end_dt = ensure_datetime(end, tz)
        if end_dt <= start_dt:
            end_dt = start_dt + timedelta(minutes=30)
        for day_offset in range(WEEKLY_DAY_COUNT):
            day = week_start + timedelta(days=day_offset)
            day_start = datetime.combine(day, time.min, tz)
            day_end = day_start + timedelta(days=1)
            segment_start = max(start_dt, day_start)
            segment_end = min(end_dt, day_end)
            if segment_start >= segment_end:
                continue
            start_hour = max(DAY_START_HOUR, hour_float(segment_start))
            raw_end_hour = 24.0 if segment_end == day_end else hour_float(segment_end)
            end_hour = min(DAY_END_HOUR, raw_end_hour)
            if end_hour <= DAY_START_HOUR or start_hour >= DAY_END_HOUR:
                continue
            timed.append(Event(day_offset, start_hour, end_hour, title, location, tone))

    return timed, all_day


def parse_month_events(
    raw_events: list[dict[str, Any]],
    range_start: date,
    range_end: date,
    tz: ZoneInfo,
) -> list[MonthEvent]:
    events: list[MonthEvent] = []

    for index, raw in enumerate(raw_events):
        title = clean_text(raw.get("summary") or raw.get("title") or raw.get("name") or "Untitled")
        start_value = first_value(raw, "start", "startTime", "start_time", "starts_at", "begin")
        end_value = first_value(raw, "end", "endTime", "end_time", "ends_at", "finish")
        if start_value is None:
            continue

        start, start_is_all_day = parse_temporal(start_value, tz)
        end, end_is_all_day = parse_temporal(end_value, tz) if end_value is not None else (start, start_is_all_day)
        tone = tone_for_raw_event(raw, index)

        if start_is_all_day or end_is_all_day:
            start_day = start if isinstance(start, date) and not isinstance(start, datetime) else start.date()
            end_day = end if isinstance(end, date) and not isinstance(end, datetime) else end.date()
            exclusive_end = end_day if end_day > start_day else start_day + timedelta(days=1)
            for day in date_range(max(start_day, range_start), min(exclusive_end, range_end)):
                events.append(MonthEvent(day, title, tone=tone))
            continue

        start_dt = ensure_datetime(start, tz)
        end_dt = ensure_datetime(end, tz)
        if end_dt <= start_dt:
            end_dt = start_dt + timedelta(minutes=30)
        for day in date_range(max(start_dt.date(), range_start), min(end_dt.date() + timedelta(days=1), range_end)):
            day_start = datetime.combine(day, time.min, tz)
            day_end = day_start + timedelta(days=1)
            segment_start = max(start_dt, day_start)
            segment_end = min(end_dt, day_end)
            if segment_start >= segment_end:
                continue
            events.append(
                MonthEvent(
                    day,
                    title,
                    format_month_time(segment_start),
                    tone,
                    segment_start.hour * 60 + segment_start.minute,
                )
            )

    return sorted(events, key=month_event_sort_key)


def mock_month_events(range_start: date, *, range_end: date | None = None) -> list[MonthEvent]:
    range_stop = range_end or next_month(range_start)
    events: list[MonthEvent] = []
    for event in MOCK_ALL_DAY_EVENTS:
        start_day = MOCK_WEEK_START + timedelta(days=event.start_day)
        end_day = MOCK_WEEK_START + timedelta(days=event.end_day + 1)
        for day in date_range(max(start_day, range_start), min(end_day, range_stop)):
            events.append(MonthEvent(day, event.title, tone=event.tone))

    for event in MOCK_EVENTS:
        day = MOCK_WEEK_START + timedelta(days=event.day)
        if range_start <= day < range_stop:
            events.append(
                MonthEvent(
                    day,
                    event.title,
                    short_time_label(event.start),
                    event.tone,
                    int(event.start * 60),
                )
            )

    return sorted(events, key=month_event_sort_key)


def next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def date_range(start: date, end: date):
    day = start
    while day < end:
        yield day
        day += timedelta(days=1)


def month_event_sort_key(event: MonthEvent) -> tuple[date, bool, int, str]:
    return event.day, bool(event.time_label), event.sort_minutes, event.title


def first_value(raw: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in raw:
            return raw[key]
    return None


def tone_for_raw_event(raw: dict[str, Any], index: int) -> int:
    label = clean_text(first_value(raw, "_trmnl_calendar_label", "calendarSummary", "calendarName", "calendar"))
    calendar_id = clean_text(first_value(raw, "_trmnl_calendar_id", "calendarId"))
    for value in (label, calendar_id):
        tone = CALENDAR_TONES.get(normalize_calendar_name(value))
        if tone is not None:
            return tone
    return TONE_STEPS[index % len(TONE_STEPS)]


def normalize_calendar_name(value: str) -> str:
    return value.strip().lower()


def has_event_time(raw: dict[str, Any]) -> bool:
    return any(key in raw for key in ("start", "startTime", "start_time", "starts_at", "begin"))


def parse_temporal(value: Any, tz: ZoneInfo) -> tuple[datetime | date, bool]:
    if isinstance(value, dict):
        if value.get("date"):
            return date.fromisoformat(str(value["date"])), True
        for key in ("dateTime", "datetime", "time"):
            if value.get(key):
                return parse_datetime(str(value[key]), tz), False

    if isinstance(value, str):
        if "T" not in value and len(value) == 10:
            return date.fromisoformat(value), True
        return parse_datetime(value, tz), False

    raise ValueError(f"Unsupported calendar timestamp: {value!r}")


def parse_datetime(value: str, tz: ZoneInfo) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def ensure_datetime(value: datetime | date, tz: ZoneInfo) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, time.min, tz)


def hour_float(value: datetime) -> float:
    return value.hour + value.minute / 60 + value.second / 3600


def format_month_time(value: datetime) -> str:
    return short_time_label(value.hour + value.minute / 60)


def short_time_label(hour: float) -> str:
    h = int(hour)
    m = int(round((hour - h) * 60))
    suffix = "a" if h < 12 else "p"
    label_hour = h % 12 or 12
    if m == 0:
        return f"{label_hour}{suffix}"
    return f"{label_hour}:{m:02d}{suffix}"


def local_zone() -> ZoneInfo:
    return ZoneInfo(os.environ.get("TRMNL_TIMEZONE", "America/Denver"))


def clean_text(value: Any) -> str:
    return " ".join(str(value).split())
