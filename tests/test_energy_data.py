from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from trmnl_weekly_calendar.energy_data import (
    EmporiaChannel,
    EnergyState,
    EnergyStateStore,
    TimedEnergySample,
    bounded_chunks,
    dashboard_from_state,
    derive_balance_samples,
    expected_minutes,
    load_energy_dashboard,
    merge_samples,
    mock_energy_dashboard,
    prune_state,
    refresh_energy_state,
    tv_runtime_hours,
)


UTC = timezone.utc
DENVER = ZoneInfo("America/Denver")


class FakeEmporiaClient:
    def __init__(self, now: datetime) -> None:
        self.now = now
        self._channels = [EmporiaChannel("total", "Total", "1,2,3", True)]
        self._channels.extend(
            [
                EmporiaChannel("circuit-1", "TV", "1"),
                EmporiaChannel("circuit-2", "Refridgerator", "2"),
            ]
        )
        self.chart_calls: list[tuple[str, datetime, datetime, str]] = []
        self.snapshot = {"total": 0.5, "circuit-1": 0.02, "circuit-2": 0.03}

    def channels(self):
        return self._channels

    def chart_usage(self, channel, start, end, scale):
        self.chart_calls.append((channel.key, start, end, scale))
        duration = {"1MIN": timedelta(minutes=1), "15MIN": timedelta(minutes=15), "1D": timedelta(days=1)}[scale]
        value = 0.5 if channel.is_main else (100 / 60_000 if scale == "1MIN" else 0.02)
        rows = []
        cursor = start
        while cursor < end:
            rows.append(TimedEnergySample(cursor, value))
            cursor += duration
        return rows

    def usage_snapshot(self, instant):
        return dict(self.snapshot)


class EnergyDataTests(unittest.TestCase):
    def test_samples_require_aware_time_and_incoming_replaces_duplicate(self):
        with self.assertRaises(ValueError):
            TimedEnergySample(datetime(2026, 1, 1), 1)
        stamp = datetime(2026, 1, 1, tzinfo=UTC)
        merged = merge_samples(
            [TimedEnergySample(stamp, 1), TimedEnergySample(stamp + timedelta(minutes=1), 2)],
            [TimedEnergySample(stamp, 3)],
        )
        self.assertEqual([3, 2], [item.kwh for item in merged])

    def test_tv_chunks_are_bounded_to_twelve_hours(self):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        chunks = bounded_chunks(start, start + timedelta(hours=25))
        self.assertEqual(3, len(chunks))
        self.assertTrue(all(end - begin <= timedelta(hours=12) for begin, end in chunks))
        self.assertEqual(start + timedelta(hours=25), chunks[-1][1])

    def test_tv_runtime_uses_watts_threshold_and_deduplicates_minutes(self):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        samples = [
            TimedEnergySample(start, 39 / 60_000),
            TimedEnergySample(start + timedelta(minutes=1), 40 / 60_000),
            TimedEnergySample(start + timedelta(minutes=1), 80 / 60_000),
            TimedEnergySample(start + timedelta(minutes=2), 100 / 60_000),
        ]
        self.assertAlmostEqual(2 / 60, tv_runtime_hours(samples, 40))

    def test_balance_is_aligned_clamped_and_skips_gaps(self):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        mains = [TimedEnergySample(start, 1), TimedEnergySample(start + timedelta(minutes=15), 1)]
        first = [TimedEnergySample(start, 0.4), TimedEnergySample(start + timedelta(minutes=15), 0.8)]
        second = [TimedEnergySample(start, 0.3), TimedEnergySample(start + timedelta(minutes=15), 0.5)]
        result = derive_balance_samples(mains, [first, second])
        self.assertEqual([0.3, 0.0], [round(item.kwh, 4) for item in result])
        partial = derive_balance_samples(mains, [first, second[:1]])
        self.assertEqual(1, len(partial))
        self.assertEqual(start, partial[0].timestamp)

    def test_dst_days_have_correct_expected_minutes(self):
        spring = date(2026, 3, 8)
        fall = date(2026, 11, 1)
        after = datetime(2026, 12, 1, tzinfo=UTC)
        self.assertEqual(23 * 60, expected_minutes(spring, DENVER, after))
        self.assertEqual(25 * 60, expected_minutes(fall, DENVER, after))

    def test_cache_round_trip_private_mode_and_corruption_quarantine(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "state.json"
            store = EnergyStateStore(path)
            state = EnergyState(
                channels=[EmporiaChannel("total", "Total", "1,2,3", True)],
                quarter_samples={
                    "total": [TimedEnergySample(datetime(2026, 1, 1, tzinfo=UTC), 1.25)]
                },
                daily_mains={date(2026, 1, 1): 12.5},
                updated_at=datetime(2026, 1, 2, tzinfo=UTC),
            )
            store.save(state)
            loaded = store.load()
            self.assertIsNotNone(loaded)
            self.assertEqual(1.25, loaded.quarter_samples["total"][0].kwh)
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
            path.write_text("{password: definitely-not-logged}", encoding="utf-8")
            self.assertIsNone(store.load())
            self.assertFalse(path.exists())
            self.assertEqual(1, len(list(path.parent.glob("state.json.corrupt-*"))))

    def test_pruning_keeps_three_quarter_days_and_eight_minute_dates(self):
        now = datetime(2026, 8, 7, 12, tzinfo=UTC)
        state = EnergyState(
            quarter_samples={
                "total": [
                    TimedEnergySample(now - timedelta(days=4), 1),
                    TimedEnergySample(now - timedelta(days=2), 2),
                ]
            },
            tv_minutes=[
                TimedEnergySample(now - timedelta(days=9), 0),
                TimedEnergySample(now - timedelta(days=7), 0),
            ],
            daily_mains={date(2026, 7, 1): 1, date(2026, 8, 6): 2},
        )
        prune_state(state, now)
        self.assertEqual([2], [item.kwh for item in state.quarter_samples["total"]])
        self.assertEqual(1, len(state.tv_minutes))
        self.assertEqual({date(2026, 8, 6): 2}, state.daily_mains)

    def test_refresh_backfills_tv_in_bounded_chunks_and_builds_contract(self):
        now = datetime(2026, 8, 7, 18, tzinfo=UTC)
        fake = FakeEmporiaClient(now)
        state = refresh_energy_state(fake, None, now)
        minute_calls = [call for call in fake.chart_calls if call[3] == "1MIN"]
        self.assertGreater(len(minute_calls), 1)
        self.assertTrue(all(end - start <= timedelta(hours=12) for _, start, end, _ in minute_calls))
        dashboard = dashboard_from_state(state, now, source="emporia")
        self.assertEqual(["total", "circuit-1", "circuit-2", "balance"], [row.key for row in dashboard.series])
        self.assertEqual("Refrigerator", dashboard.series[2].label)
        self.assertEqual(7, len(dashboard.daily_mains))
        self.assertEqual(7, len(dashboard.daily_tv_hours))

    def test_refresh_uses_incremental_snapshot_and_replaces_partial_bucket(self):
        now = datetime(2026, 8, 7, 18, 7, tzinfo=UTC)
        fake = FakeEmporiaClient(now)
        start = datetime(2026, 8, 6, 6, tzinfo=UTC)  # Denver midnight.
        rows = [TimedEnergySample(start + index * timedelta(minutes=15), 0.1) for index in range(145)]
        state = EnergyState(
            channels=list(fake.channels()),
            quarter_samples={channel.key: list(rows) for channel in fake.channels()},
        )
        refreshed = refresh_energy_state(fake, state, now)
        self.assertFalse(any(scale == "15MIN" for _, _, _, scale in fake.chart_calls))
        bucket = datetime(2026, 8, 7, 18, tzinfo=UTC)
        total = {item.timestamp: item.kwh for item in refreshed.quarter_samples["total"]}
        self.assertEqual(0.5, total[bucket])

    def test_no_credentials_returns_mock_and_configured_failure_is_unavailable(self):
        now = datetime(2026, 8, 7, tzinfo=UTC)
        with patch.dict(os.environ, {}, clear=True):
            result = load_energy_dashboard(now=now)
        self.assertEqual("mock", result.source)
        self.assertEqual(16, len(result.series))
        self.assertEqual(7, len(result.daily_tv_hours))

        class Broken:
            def channels(self):
                raise RuntimeError("secret-bearing provider failure")

        with tempfile.TemporaryDirectory() as directory:
            with self.assertLogs("trmnl_weekly_calendar.energy_data", level="WARNING") as logs:
                unavailable = load_energy_dashboard(
                    now=now,
                    client=Broken(),
                    store=EnergyStateStore(Path(directory) / "missing.json"),
                )
            self.assertEqual("unavailable", unavailable.source)
            self.assertNotIn("secret-bearing", " ".join(logs.output))

        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"EMPORIA_USERNAME": "configured"}, clear=True):
                incomplete = load_energy_dashboard(
                    now=now,
                    store=EnergyStateStore(Path(directory) / "missing.json"),
                )
            self.assertEqual("unavailable", incomplete.source)

    def test_mock_is_deterministic(self):
        now = datetime(2026, 8, 7, 20, tzinfo=UTC)
        first = mock_energy_dashboard(now)
        second = mock_energy_dashboard(now)
        self.assertEqual(first, second)
        self.assertEqual("total", first.series[0].key)
        self.assertEqual("balance", first.series[-1].key)


if __name__ == "__main__":
    unittest.main()
