from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Protocol, Sequence
from zoneinfo import ZoneInfo


LOGGER = logging.getLogger(__name__)
UTC = timezone.utc
DEFAULT_TIMEZONE = "America/Denver"
DEFAULT_TV_CHANNEL_NAME = "TV"
DEFAULT_TV_THRESHOLD_WATTS = 40.0
QUARTER = timedelta(minutes=15)
MINUTE = timedelta(minutes=1)
TV_CHUNK = timedelta(hours=12)
STATE_VERSION = 1


@dataclass(frozen=True, order=True)
class TimedEnergySample:
    """One energy bucket. Timestamps are always stored in UTC."""

    timestamp: datetime
    kwh: float

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("energy sample timestamps must be timezone-aware")
        object.__setattr__(self, "timestamp", self.timestamp.astimezone(UTC))


@dataclass(frozen=True)
class AveragePowerPoint:
    """Average power for one 15-minute bucket, plotted at its local midpoint."""

    hour: float
    kw: float
    timestamp: datetime


@dataclass(frozen=True)
class DailyEnergyValue:
    day: date
    value: float
    complete: bool


@dataclass(frozen=True)
class EnergyPlotSeries:
    key: str
    label: str
    channel_number: str | None
    yesterday: tuple[AveragePowerPoint, ...]
    today: tuple[AveragePowerPoint, ...]
    yesterday_kwh: float
    today_kwh: float
    yesterday_complete: bool
    today_complete: bool


@dataclass(frozen=True)
class EnergyDashboardData:
    timezone: str
    display_dates: tuple[date, ...]
    today: date
    yesterday: date
    series: tuple[EnergyPlotSeries, ...]
    daily_mains: tuple[DailyEnergyValue, ...]
    daily_tv_hours: tuple[DailyEnergyValue, ...]
    source: str
    freshness: datetime | None
    message: str = ""


@dataclass(frozen=True)
class EmporiaChannel:
    """Provider-neutral channel identity; native identifiers stay in the adapter."""

    key: str
    label: str
    channel_number: str
    is_main: bool = False


class EmporiaClient(Protocol):
    def channels(self) -> Sequence[EmporiaChannel]: ...

    def chart_usage(
        self,
        channel: EmporiaChannel,
        start: datetime,
        end: datetime,
        scale: str,
    ) -> list[TimedEnergySample]: ...

    def usage_snapshot(self, instant: datetime) -> dict[str, float]: ...


class PyEmVueAdapter:
    """Small read-only boundary around the unofficial pyemvue package."""

    def __init__(self, username: str, password: str, token_file: Path) -> None:
        from pyemvue import PyEmVue

        ensure_private_parent(token_file)
        self._vue = PyEmVue()
        self._vue.login(
            username=username,
            password=password,
            token_storage_file=str(token_file),
        )
        enforce_private_file(token_file)
        self._native: dict[str, Any] = {}
        self._device_gids: list[int] = []
        self._channels = self._discover_channels()

    def _discover_channels(self) -> tuple[EmporiaChannel, ...]:
        devices = self._vue.get_devices()
        candidates: dict[tuple[int, str], tuple[Any, str]] = {}
        for device in devices:
            gid = int(device.device_gid)
            if gid not in self._device_gids:
                self._device_gids.append(gid)
            for channel in device.channels:
                number = str(channel.channel_num)
                name = str(channel.name or "").strip()
                candidates[(gid, number)] = (channel, name)

        result: list[EmporiaChannel] = []
        main_seen = False
        for (_gid, number), (native, name) in candidates.items():
            is_main = name.casefold() == "main" or str(getattr(native, "type", "")).casefold() == "main"
            if is_main and main_seen:
                continue
            # Emporia exposes a merged circuit and its source channel as separate
            # device metadata records. Keep the parent aggregate only or the same
            # load (for example a two-pole dryer) appears twice in the dashboard.
            if not is_main and getattr(native, "parent_channel_num", None) not in {None, ""}:
                continue
            if not name and not is_main:
                continue
            key = "total" if is_main else f"circuit-{channel_slug(number)}"
            public = EmporiaChannel(key, "Total" if is_main else name, number, is_main)
            self._native[key] = native
            result.append(public)
            main_seen = main_seen or is_main
        result.sort(key=lambda item: (not item.is_main, channel_sort_key(item.channel_number)))
        return tuple(result)

    def channels(self) -> Sequence[EmporiaChannel]:
        return self._channels

    def chart_usage(
        self,
        channel: EmporiaChannel,
        start: datetime,
        end: datetime,
        scale: str,
    ) -> list[TimedEnergySample]:
        values, first = self._vue.get_chart_usage(
            self._native[channel.key],
            start=start,
            end=end,
            scale=scale,
            unit="KilowattHours",
        )
        if first is None:
            return []
        first = aware_utc(first)
        if scale == "1D":
            local_first_day = first.astimezone(ZoneInfo(configured_timezone())).date()
            timestamps = [
                local_midnight(
                    local_first_day + timedelta(days=index),
                    ZoneInfo(configured_timezone()),
                ).astimezone(UTC)
                for index in range(len(values))
            ]
        else:
            step = scale_duration(scale)
            timestamps = [first + index * step for index in range(len(values))]
        return [
            TimedEnergySample(timestamps[index], float(value))
            for index, value in enumerate(values)
            if value is not None and math.isfinite(float(value))
        ]

    def usage_snapshot(self, instant: datetime) -> dict[str, float]:
        payload = self._vue.get_device_list_usage(
            deviceGids=self._device_gids,
            instant=instant,
            scale="15MIN",
            unit="KilowattHours",
        )
        by_native = {
            (int(native.device_gid), str(native.channel_num)): key
            for key, native in self._native.items()
        }
        result: dict[str, float] = {}
        for gid, device in payload.items():
            for number, channel in device.channels.items():
                key = by_native.get((int(gid), str(number)))
                if key is not None and channel.usage is not None:
                    result[key] = float(channel.usage)
        return result


@dataclass
class EnergyState:
    timezone: str = DEFAULT_TIMEZONE
    channels: list[EmporiaChannel] = field(default_factory=list)
    quarter_samples: dict[str, list[TimedEnergySample]] = field(default_factory=dict)
    tv_minutes: list[TimedEnergySample] = field(default_factory=list)
    daily_mains: dict[date, float] = field(default_factory=dict)
    updated_at: datetime | None = None


class EnergyStateStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or energy_state_path()
        self._lock = RLock()

    def load(self) -> EnergyState | None:
        with self._lock:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                if payload.get("version") != STATE_VERSION:
                    raise ValueError("unsupported state version")
                return state_from_json(payload)
            except FileNotFoundError:
                return None
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                quarantine_corrupt(self.path)
                return None

    def save(self, state: EnergyState) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            payload = json.dumps(state_to_json(state), separators=(",", ":"), sort_keys=True)
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{self.path.name}.", dir=self.path.parent
            )
            try:
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
                self.path.chmod(0o600)
            finally:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass


_LOAD_LOCK = RLock()


def load_energy_dashboard(
    *,
    force: bool = False,
    now: datetime | None = None,
    client: EmporiaClient | None = None,
    store: EnergyStateStore | None = None,
) -> EnergyDashboardData:
    """Load live energy data, falling back safely to cache or deterministic mock data."""

    requested_now = aware_utc(now or datetime.now(UTC))
    username = os.environ.get("EMPORIA_USERNAME", "").strip()
    password = os.environ.get("EMPORIA_PASSWORD", "").strip()
    credentials_present = bool(username or password)
    if client is None and not credentials_present:
        return mock_energy_dashboard(requested_now)

    state_store = store or EnergyStateStore()
    with _LOAD_LOCK:
        cached = state_store.load()
        if client is None and not (username and password):
            if cached is not None:
                return dashboard_from_state(
                    cached,
                    requested_now,
                    source="cache-stale",
                    message="Emporia credentials are incomplete; showing last saved data",
                )
            return unavailable_energy_dashboard(requested_now)
        try:
            active_client = client or PyEmVueAdapter(username, password, emporia_token_file())
            state = refresh_energy_state(active_client, cached, requested_now, force=force)
            state_store.save(state)
            return dashboard_from_state(state, requested_now, source="emporia")
        except Exception as exc:
            LOGGER.warning(
                "energy refresh failed provider=emporia error=%s",
                type(exc).__name__,
            )
            if cached is not None:
                return dashboard_from_state(
                    cached,
                    requested_now,
                    source="cache-stale",
                    message="Emporia unavailable; showing last saved data",
                )
            return unavailable_energy_dashboard(requested_now)


def refresh_energy_state(
    client: EmporiaClient,
    state: EnergyState | None,
    now: datetime,
    *,
    force: bool = False,
) -> EnergyState:
    channels = canonical_channels(client.channels())
    main = next((channel for channel in channels if channel.is_main), None)
    if main is None:
        raise RuntimeError("Emporia did not return a mains channel")
    physical = [channel for channel in channels if not channel.is_main]
    if not physical:
        raise RuntimeError("Emporia did not return physical circuits")

    tz_name = configured_timezone()
    tz = ZoneInfo(tz_name)
    today = now.astimezone(tz).date()
    yesterday = today - timedelta(days=1)
    window_start = local_midnight(yesterday, tz).astimezone(UTC)
    window_end = local_midnight(today + timedelta(days=1), tz).astimezone(UTC)
    result = state or EnergyState(timezone=tz_name)
    result.timezone = tz_name
    result.channels = list(channels)

    latest_completed = floor_instant(now, QUARTER) - QUARTER
    needs_backfill = force or any(
        not samples_cover(
            result.quarter_samples.get(channel.key, []),
            window_start,
            latest_completed,
        )
        for channel in channels
    )
    if needs_backfill:
        for channel in channels:
            fetched = client.chart_usage(channel, window_start, min(window_end, now), "15MIN")
            result.quarter_samples[channel.key] = merge_samples(
                result.quarter_samples.get(channel.key, []), fetched
            )
    else:
        bucket = floor_instant(now, QUARTER)
        snapshot = client.usage_snapshot(now)
        for channel in channels:
            if channel.key in snapshot:
                result.quarter_samples[channel.key] = merge_samples(
                    result.quarter_samples.get(channel.key, []),
                    [TimedEnergySample(bucket, snapshot[channel.key])],
                )

    tv = find_tv_channel(physical)
    if tv is not None:
        tv_start = local_midnight(today - timedelta(days=7), tz).astimezone(UTC)
        if not force and result.tv_minutes:
            tv_start = max(tv_start, max(sample.timestamp for sample in result.tv_minutes) - MINUTE)
        for chunk_start, chunk_end in bounded_chunks(tv_start, now, TV_CHUNK):
            fetched = client.chart_usage(tv, chunk_start, chunk_end, "1MIN")
            result.tv_minutes = merge_samples(result.tv_minutes, fetched)

    daily_start_day = today - timedelta(days=6)
    completed_days = {daily_start_day + timedelta(days=offset) for offset in range(6)}
    if force or not completed_days.issubset(result.daily_mains):
        daily_start = local_midnight(daily_start_day, tz).astimezone(UTC)
        daily_end = local_midnight(today, tz).astimezone(UTC)
        daily = client.chart_usage(main, daily_start, daily_end, "1D")
        for sample in daily:
            sample_day = sample.timestamp.astimezone(tz).date()
            if sample_day in completed_days:
                result.daily_mains[sample_day] = sample.kwh

    result.updated_at = now
    prune_state(result, now)
    return result


def dashboard_from_state(
    state: EnergyState,
    now: datetime,
    *,
    source: str,
    message: str = "",
) -> EnergyDashboardData:
    tz = ZoneInfo(state.timezone)
    today = now.astimezone(tz).date()
    yesterday = today - timedelta(days=1)
    display_dates = tuple(today - timedelta(days=offset) for offset in range(6, -1, -1))
    channels = canonical_channels(state.channels)
    physical = [item for item in channels if not item.is_main]
    plots: list[EnergyPlotSeries] = []
    for channel in channels:
        samples = state.quarter_samples.get(channel.key, [])
        plots.append(make_plot_series(channel, samples, yesterday, today, tz, now))

    balance_samples = derive_balance_samples(
        state.quarter_samples.get("total", []),
        [state.quarter_samples.get(channel.key, []) for channel in physical],
    )
    balance = EmporiaChannel("balance", "Balance", "", False)
    plots.append(make_plot_series(balance, balance_samples, yesterday, today, tz, now))

    mains_values: list[DailyEnergyValue] = []
    mains_samples = state.quarter_samples.get("total", [])
    for day in display_dates:
        cached_value = state.daily_mains.get(day)
        day_samples = samples_for_day(mains_samples, day, tz)
        value = cached_value if cached_value is not None else sum(item.kwh for item in day_samples)
        mains_values.append(DailyEnergyValue(day, value, day < today and cached_value is not None))

    threshold = configured_tv_threshold()
    tv_values: list[DailyEnergyValue] = []
    for day in display_dates:
        minute_samples = samples_for_day(state.tv_minutes, day, tz)
        hours = tv_runtime_hours(minute_samples, threshold)
        expected = expected_minutes(day, tz, now)
        complete = day < today and len(minute_samples) >= expected * 0.9
        tv_values.append(DailyEnergyValue(day, hours, complete))

    return EnergyDashboardData(
        timezone=state.timezone,
        display_dates=display_dates,
        today=today,
        yesterday=yesterday,
        series=tuple(plots),
        daily_mains=tuple(mains_values),
        daily_tv_hours=tuple(tv_values),
        source=source,
        freshness=state.updated_at,
        message=message,
    )


def mock_energy_dashboard(now: datetime | None = None) -> EnergyDashboardData:
    now = aware_utc(now or datetime(2026, 8, 7, 19, 30, tzinfo=ZoneInfo(DEFAULT_TIMEZONE)))
    tz = ZoneInfo(DEFAULT_TIMEZONE)
    today = now.astimezone(tz).date()
    channels = [EmporiaChannel("total", "Total", "1,2,3", True)]
    circuits = [
        ("1", "WiFi"),
        ("2", "1st Floor Office"),
        ("3", "TV"),
        ("4", "Air Conditioner"),
        ("5", "Furnace + Radon Fan"),
        ("8", "Oven"),
        ("9", "Microwave + Range Hood"),
        ("10", "Dishwasher"),
        ("11", "Sump Pump"),
        ("12", "Refrigerator"),
        ("13", "Water Heater"),
        ("15", "Clothes Washer"),
        ("97", "EV + Addition Subpanel"),
        ("98", "Dryer"),
    ]
    channels.extend(
        EmporiaChannel(f"circuit-{channel_slug(number)}", label, number, False)
        for number, label in circuits
    )
    state = EnergyState(timezone=DEFAULT_TIMEZONE, channels=channels, updated_at=now)
    day_start = local_midnight(today - timedelta(days=7), tz).astimezone(UTC)
    for channel_index, channel in enumerate(channels):
        samples: list[TimedEnergySample] = []
        for index in range(int((now - day_start) / QUARTER) + 1):
            stamp = day_start + index * QUARTER
            local = stamp.astimezone(tz)
            hour = local.hour + local.minute / 60
            base = 0.014 + channel_index * 0.0017
            wave = max(0.12, math.sin((hour - 6) * math.pi / 18))
            if channel.is_main:
                value = 0.12 + 0.30 * wave + (0.18 if 16 <= hour <= 21 else 0)
            else:
                value = base * (0.35 + wave) * (1 + ((local.toordinal() + channel_index) % 4) * 0.08)
            samples.append(TimedEnergySample(stamp, value))
        state.quarter_samples[channel.key] = samples

    # Keep the synthetic physical circuits below mains at every bucket so the
    # derived Balance is realistic and monotonic instead of disappearing when
    # mock circuit shapes happen to overlap.
    mains_by_stamp = {
        sample.timestamp: sample.kwh for sample in state.quarter_samples["total"]
    }
    physical_keys = [channel.key for channel in channels if not channel.is_main]
    physical_by_stamp = {
        key: {sample.timestamp: sample.kwh for sample in state.quarter_samples[key]}
        for key in physical_keys
    }
    physical_totals = {
        stamp: sum(physical_by_stamp[key].get(stamp, 0.0) for key in physical_keys)
        for stamp in mains_by_stamp
    }
    for key in physical_keys:
        state.quarter_samples[key] = [
            TimedEnergySample(
                sample.timestamp,
                sample.kwh
                * min(
                    1.0,
                    mains_by_stamp[sample.timestamp]
                    * 0.94
                    / max(physical_totals[sample.timestamp], 1e-9),
                ),
            )
            for sample in state.quarter_samples[key]
        ]

    for index in range(int((now - day_start) / MINUTE) + 1):
        stamp = day_start + index * MINUTE
        local = stamp.astimezone(tz)
        on = 18 <= local.hour < 22 or (local.weekday() >= 5 and 10 <= local.hour < 12)
        state.tv_minutes.append(TimedEnergySample(stamp, (92 if on else 7) / 60_000))
    for offset in range(1, 7):
        day = today - timedelta(days=offset)
        state.daily_mains[day] = 20.0 + ((day.toordinal() * 7) % 19) * 1.15
    return dashboard_from_state(state, now, source="mock", message="Sample energy data")


def unavailable_energy_dashboard(now: datetime) -> EnergyDashboardData:
    tz = ZoneInfo(configured_timezone())
    today = now.astimezone(tz).date()
    days = tuple(today - timedelta(days=offset) for offset in range(6, -1, -1))
    return EnergyDashboardData(
        timezone=str(tz.key),
        display_dates=days,
        today=today,
        yesterday=today - timedelta(days=1),
        series=(),
        daily_mains=tuple(DailyEnergyValue(day, 0.0, False) for day in days),
        daily_tv_hours=tuple(DailyEnergyValue(day, 0.0, False) for day in days),
        source="unavailable",
        freshness=None,
        message="Emporia data unavailable",
    )


def make_plot_series(
    channel: EmporiaChannel,
    samples: Sequence[TimedEnergySample],
    yesterday: date,
    today: date,
    tz: ZoneInfo,
    now: datetime,
) -> EnergyPlotSeries:
    yesterday_samples = samples_for_day(samples, yesterday, tz)
    today_samples = samples_for_day(samples, today, tz)
    return EnergyPlotSeries(
        key=channel.key,
        label=normalized_label(channel.label),
        channel_number=channel.channel_number or None,
        yesterday=average_power_points(yesterday_samples, tz),
        today=average_power_points(today_samples, tz),
        yesterday_kwh=sum(item.kwh for item in yesterday_samples),
        today_kwh=sum(item.kwh for item in today_samples),
        yesterday_complete=sample_coverage(yesterday_samples, yesterday, tz, now) >= 0.9,
        today_complete=False,
    )


def average_power_points(
    samples: Sequence[TimedEnergySample], tz: ZoneInfo
) -> tuple[AveragePowerPoint, ...]:
    result: list[AveragePowerPoint] = []
    for sample in deduplicate_samples(samples):
        midpoint = sample.timestamp + QUARTER / 2
        local = midpoint.astimezone(tz)
        hour = local.hour + local.minute / 60 + local.second / 3600
        result.append(
            AveragePowerPoint(
                hour=min(24.0, hour),
                kw=max(0.0, sample.kwh * 4),
                timestamp=sample.timestamp,
            )
        )
    return tuple(result)


def derive_balance_samples(
    mains: Sequence[TimedEnergySample],
    physical_series: Sequence[Sequence[TimedEnergySample]],
) -> list[TimedEnergySample]:
    circuit_maps = [{item.timestamp: item.kwh for item in series} for series in physical_series]
    result: list[TimedEnergySample] = []
    for sample in deduplicate_samples(mains):
        if not all(sample.timestamp in values for values in circuit_maps):
            continue
        residual = sample.kwh - sum(values[sample.timestamp] for values in circuit_maps)
        result.append(TimedEnergySample(sample.timestamp, max(0.0, residual)))
    return result


def tv_runtime_hours(samples: Sequence[TimedEnergySample], threshold_watts: float) -> float:
    unique = deduplicate_samples(samples)
    return sum(1 for item in unique if item.kwh * 60_000 >= threshold_watts) / 60


def bounded_chunks(
    start: datetime, end: datetime, maximum: timedelta = TV_CHUNK
) -> list[tuple[datetime, datetime]]:
    if maximum <= timedelta(0):
        raise ValueError("maximum chunk duration must be positive")
    chunks: list[tuple[datetime, datetime]] = []
    cursor = aware_utc(start)
    stop = aware_utc(end)
    while cursor < stop:
        chunk_end = min(cursor + maximum, stop)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end
    return chunks


def merge_samples(
    existing: Sequence[TimedEnergySample], incoming: Sequence[TimedEnergySample]
) -> list[TimedEnergySample]:
    merged = {item.timestamp: item for item in existing}
    for item in incoming:
        merged[item.timestamp] = item
    return sorted(merged.values())


def deduplicate_samples(samples: Sequence[TimedEnergySample]) -> list[TimedEnergySample]:
    return merge_samples((), samples)


def prune_state(state: EnergyState, now: datetime) -> None:
    tz = ZoneInfo(state.timezone)
    today = now.astimezone(tz).date()
    quarter_cutoff = local_midnight(today - timedelta(days=2), tz).astimezone(UTC)
    minute_cutoff = local_midnight(today - timedelta(days=7), tz).astimezone(UTC)
    state.quarter_samples = {
        key: [sample for sample in samples if sample.timestamp >= quarter_cutoff]
        for key, samples in state.quarter_samples.items()
    }
    state.tv_minutes = [sample for sample in state.tv_minutes if sample.timestamp >= minute_cutoff]
    state.daily_mains = {
        day: value for day, value in state.daily_mains.items() if day >= today - timedelta(days=7)
    }


def samples_for_day(
    samples: Sequence[TimedEnergySample], day: date, tz: ZoneInfo
) -> list[TimedEnergySample]:
    start = local_midnight(day, tz).astimezone(UTC)
    end = local_midnight(day + timedelta(days=1), tz).astimezone(UTC)
    return [item for item in deduplicate_samples(samples) if start <= item.timestamp < end]


def sample_coverage(
    samples: Sequence[TimedEnergySample], day: date, tz: ZoneInfo, now: datetime
) -> float:
    expected = max(1, expected_minutes(day, tz, now) // 15)
    return min(1.0, len(deduplicate_samples(samples)) / expected)


def expected_minutes(day: date, tz: ZoneInfo, now: datetime) -> int:
    start = local_midnight(day, tz).astimezone(UTC)
    end = local_midnight(day + timedelta(days=1), tz).astimezone(UTC)
    if day == now.astimezone(tz).date():
        end = min(end, now)
    return max(0, round((end - start).total_seconds() / 60))


def canonical_channels(channels: Sequence[EmporiaChannel]) -> list[EmporiaChannel]:
    unique: dict[str, EmporiaChannel] = {}
    for channel in channels:
        unique[channel.key] = channel
    return sorted(unique.values(), key=lambda item: (not item.is_main, channel_sort_key(item.channel_number)))


def find_tv_channel(channels: Sequence[EmporiaChannel]) -> EmporiaChannel | None:
    wanted = os.environ.get("EMPORIA_TV_CHANNEL_NAME", DEFAULT_TV_CHANNEL_NAME).strip().casefold()
    return next((item for item in channels if item.label.strip().casefold() == wanted), None)


def configured_tv_threshold() -> float:
    try:
        value = float(os.environ.get("EMPORIA_TV_ON_THRESHOLD_WATTS", DEFAULT_TV_THRESHOLD_WATTS))
    except ValueError:
        return DEFAULT_TV_THRESHOLD_WATTS
    return value if value >= 0 else DEFAULT_TV_THRESHOLD_WATTS


def configured_timezone() -> str:
    value = os.environ.get("EMPORIA_TIMEZONE", DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    ZoneInfo(value)
    return value


def energy_state_path() -> Path:
    configured = os.environ.get("TRMNL_ENERGY_STATE_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[2] / "outputs" / "server-cache" / "energy-state.json"


def emporia_token_file() -> Path:
    configured = os.environ.get("EMPORIA_TOKEN_FILE", "").strip()
    if configured:
        return Path(configured).expanduser()
    state_home = os.environ.get("XDG_STATE_HOME", "").strip()
    root = Path(state_home).expanduser() if state_home else Path.home() / ".local" / "state"
    return root / "trmnl-calendar" / "emporia-tokens.json"


def ensure_private_parent(path: Path) -> None:
    existed = path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not existed:
        try:
            path.parent.chmod(0o700)
        except OSError:
            pass


def enforce_private_file(path: Path) -> None:
    try:
        path.chmod(0o600)
    except FileNotFoundError:
        return


def state_to_json(state: EnergyState) -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "timezone": state.timezone,
        "channels": [
            {
                "key": item.key,
                "label": item.label,
                "channel_number": item.channel_number,
                "is_main": item.is_main,
            }
            for item in state.channels
        ],
        "quarter_samples": {
            key: [[iso_z(item.timestamp), item.kwh] for item in samples]
            for key, samples in state.quarter_samples.items()
        },
        "tv_minutes": [[iso_z(item.timestamp), item.kwh] for item in state.tv_minutes],
        "daily_mains": {day.isoformat(): value for day, value in state.daily_mains.items()},
        "updated_at": iso_z(state.updated_at) if state.updated_at else None,
    }


def state_from_json(payload: dict[str, Any]) -> EnergyState:
    return EnergyState(
        timezone=str(payload["timezone"]),
        channels=[EmporiaChannel(**item) for item in payload.get("channels", [])],
        quarter_samples={
            key: [TimedEnergySample(parse_instant(stamp), float(value)) for stamp, value in rows]
            for key, rows in payload.get("quarter_samples", {}).items()
        },
        tv_minutes=[
            TimedEnergySample(parse_instant(stamp), float(value))
            for stamp, value in payload.get("tv_minutes", [])
        ],
        daily_mains={date.fromisoformat(day): float(value) for day, value in payload.get("daily_mains", {}).items()},
        updated_at=parse_instant(payload["updated_at"]) if payload.get("updated_at") else None,
    )


def quarantine_corrupt(path: Path) -> None:
    if not path.exists():
        return
    suffix = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = path.with_name(f"{path.name}.corrupt-{suffix}")
    try:
        path.replace(destination)
    except OSError:
        pass


def samples_cover(
    samples: Sequence[TimedEnergySample],
    start: datetime,
    end: datetime | None = None,
) -> bool:
    if not samples or min(item.timestamp for item in samples) > start + QUARTER:
        return False
    return end is None or max(item.timestamp for item in samples) >= end


def local_midnight(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=tz)


def floor_instant(value: datetime, duration: timedelta) -> datetime:
    value = aware_utc(value)
    seconds = int(duration.total_seconds())
    return datetime.fromtimestamp(int(value.timestamp()) // seconds * seconds, tz=UTC)


def aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def scale_duration(scale: str) -> timedelta:
    durations = {"1MIN": MINUTE, "15MIN": QUARTER, "1H": timedelta(hours=1), "1D": timedelta(days=1)}
    try:
        return durations[scale]
    except KeyError as exc:
        raise ValueError(f"unsupported Emporia scale: {scale}") from exc


def parse_instant(value: str) -> datetime:
    return aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def iso_z(value: datetime) -> str:
    return aware_utc(value).isoformat().replace("+00:00", "Z")


def channel_slug(number: str) -> str:
    return "-".join(part.strip() for part in number.split(",") if part.strip())


def channel_sort_key(number: str) -> tuple[int, str]:
    try:
        return int(number.split(",", 1)[0]), number
    except ValueError:
        return 10_000, number


def normalized_label(label: str) -> str:
    replacements = {"refridgerator": "Refrigerator", "drier": "Dryer"}
    return replacements.get(label.strip().casefold(), label.strip())


def configuration_fingerprint(channels: Sequence[EmporiaChannel]) -> str:
    material = "|".join(f"{item.key}:{item.label}" for item in canonical_channels(channels))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:10]
