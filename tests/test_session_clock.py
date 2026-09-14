"""Unit tests for app.session_clock (no network)."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from app.session_clock import (
    hour_of_week_ranges,
    power_hour_bounds,
    tag_bar,
    waiting_deal_day_outcomes,
    waiting_deal_state,
)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")


def _bar(dt: datetime, *, high: float, low: float, close: float | None = None) -> dict:
    mid = close if close is not None else (high + low) / 2.0
    return {
        "time": _iso(dt),
        "open": mid,
        "high": high,
        "low": low,
        "close": mid,
        "volume": 10,
        "complete": True,
    }


class TestPowerHourBounds(unittest.TestCase):
    def test_summer_matches_book_gmt_window(self) -> None:
        start, end = power_hour_bounds(date(2024, 7, 15), clock="frankfurt")
        self.assertEqual(start, datetime(2024, 7, 15, 6, 0, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2024, 7, 15, 7, 0, tzinfo=timezone.utc))

    def test_winter_shifts_one_hour(self) -> None:
        start, end = power_hour_bounds(date(2024, 1, 15), clock="frankfurt")
        self.assertEqual(start, datetime(2024, 1, 15, 7, 0, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2024, 1, 15, 8, 0, tzinfo=timezone.utc))

    def test_utc_fixed_ignores_dst(self) -> None:
        start, end = power_hour_bounds(date(2024, 1, 15), clock="utc_fixed")
        self.assertEqual(start.hour, 6)
        self.assertEqual(end.hour, 7)


class TestTagBar(unittest.TestCase):
    def test_london_overlap_weekday(self) -> None:
        tagged = tag_bar("2024-07-15T13:00:00.000000000Z")
        assert tagged is not None
        self.assertEqual(tagged["session"], "overlap_london_ny")
        self.assertEqual(tagged["london_date"], "2024-07-15")
        self.assertFalse(tagged["weekend"])


class TestWaitingDealState(unittest.TestCase):
    def test_hunt_up_then_reverse_is_short(self) -> None:
        origin = datetime(2024, 7, 15, 6, 0, tzinfo=timezone.utc)
        bars = [
            _bar(origin, high=1.2720, low=1.2705),
            _bar(origin.replace(minute=15), high=1.2718, low=1.2700),
            _bar(origin.replace(minute=30), high=1.2715, low=1.2702),
            _bar(origin.replace(minute=45), high=1.2716, low=1.2704),
            _bar(origin.replace(hour=7), high=1.2755, low=1.2715),
            _bar(origin.replace(hour=8), high=1.2710, low=1.2685),
        ]
        state = waiting_deal_state(bars, pip=0.0001)
        self.assertTrue(state["power_hour_complete"])
        self.assertEqual(state["hunt_side"], "up")
        self.assertTrue(state["reversed"])
        self.assertEqual(state["pending_side"], "short")
        self.assertAlmostEqual(state["pending_entry"], 1.2690)
        self.assertAlmostEqual(state["pending_stop"], 1.2725)

    def test_hunt_without_reverse_does_not_complete(self) -> None:
        origin = datetime(2024, 7, 15, 6, 0, tzinfo=timezone.utc)
        bars = [
            _bar(origin, high=1.2720, low=1.2700),
            _bar(origin.replace(hour=7), high=1.2755, low=1.2715),
            _bar(origin.replace(hour=8), high=1.2740, low=1.2710),
        ]
        state = waiting_deal_state(bars, pip=0.0001)
        self.assertEqual(state["hunt_side"], "up")
        self.assertFalse(state["reversed"])

    def test_weekend_skips(self) -> None:
        origin = datetime(2024, 7, 13, 6, 0, tzinfo=timezone.utc)  # Saturday
        bars = [_bar(origin, high=1.27, low=1.26)]
        state = waiting_deal_state(bars, pip=0.0001)
        self.assertIn("weekend", state["reason"])

    def test_hour_of_week_ranges(self) -> None:
        bars = [
            _bar(
                datetime(2024, 7, 15, 6, 0, tzinfo=timezone.utc),
                high=1.2720,
                low=1.2700,
            ),
            _bar(
                datetime(2024, 7, 15, 13, 0, tzinfo=timezone.utc),
                high=1.2800,
                low=1.2700,
            ),
        ]
        rows = hour_of_week_ranges(bars, 0.0001)
        by_hour = {r["hour_utc"]: r["mean_range_pips"] for r in rows}
        self.assertEqual(by_hour[6], 20.0)
        self.assertEqual(by_hour[13], 100.0)

    def test_day_outcomes_count_reverse(self) -> None:
        origin = datetime(2024, 7, 15, 6, 0, tzinfo=timezone.utc)
        bars = [
            _bar(origin, high=1.2720, low=1.2700),
            _bar(origin.replace(hour=7), high=1.2755, low=1.2715),
            _bar(origin.replace(hour=8), high=1.2710, low=1.2685),
        ]
        stats = waiting_deal_day_outcomes(bars, 0.0001)
        self.assertEqual(stats["days_with_power_hour"], 1)
        self.assertEqual(stats["hunts"], 1)
        self.assertEqual(stats["reverses"], 1)
        self.assertEqual(stats["hunt_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
