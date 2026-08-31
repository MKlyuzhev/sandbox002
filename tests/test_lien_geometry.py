"""Unit tests for app.lien_geometry (no network)."""

from __future__ import annotations

import unittest

from app import indicators, lien_geometry
from tests.test_indicators import _bar, _trend_bars


def _flat(n: int, level: float = 1.10, half: float = 0.001) -> list[dict]:
    return [_bar(i, level, half=half) for i in range(n)]


class TestPerfectOrderAge(unittest.TestCase):
    def test_none_when_not_stacked(self) -> None:
        series = [None, "up", None]
        self.assertIsNone(lien_geometry.perfect_order_age(series[0:1]))
        self.assertIsNone(lien_geometry.perfect_order_age([None]))

    def test_counts_consecutive_tail(self) -> None:
        series = [None, "up", "up", "up", "up", "up"]
        self.assertEqual(lien_geometry.perfect_order_age(series), 5)

    def test_resets_on_break(self) -> None:
        series = ["up", "up", "down", "down"]
        self.assertEqual(lien_geometry.perfect_order_age(series), 2)


class TestPriorDay(unittest.TestCase):
    def test_uses_bar_before_last(self) -> None:
        bars = [
            {"open": 1.0, "high": 1.2, "low": 0.9, "close": 1.1},
            {"open": 1.1, "high": 1.3, "low": 1.05, "close": 1.2},
        ]
        pd = lien_geometry.prior_day_high_low(bars)
        self.assertEqual(pd["high"], 1.2)
        self.assertEqual(pd["low"], 0.9)


class TestBreakout20(unittest.TestCase):
    def test_first_touch_is_not_rebreak(self) -> None:
        bars = _flat(25, 1.10, half=0.0005)
        # Last bar spikes to a new 20-day high — first touch.
        bars[-1] = {
            "time": "t24",
            "open": 1.10,
            "high": 1.20,
            "low": 1.09,
            "close": 1.19,
            "volume": None,
        }
        state = lien_geometry.breakout_20_state(bars)
        self.assertFalse(state["rebreak"])
        self.assertEqual(state["side"], "long")

    def test_rebreak_after_two_day_pullback(self) -> None:
        level = 1.10
        bars = _flat(20, level, half=0.0004)
        # Bar 20: tag 20-day high (extreme).
        bars.append(
            {
                "time": "t20",
                "open": level,
                "high": 1.15,
                "low": 1.09,
                "close": 1.14,
                "volume": None,
            }
        )
        # Bars 21-22: pullback (lower highs).
        bars.append(
            {
                "time": "t21",
                "open": 1.13,
                "high": 1.135,
                "low": 1.10,
                "close": 1.11,
                "volume": None,
            }
        )
        bars.append(
            {
                "time": "t22",
                "open": 1.11,
                "high": 1.12,
                "low": 1.09,
                "close": 1.10,
                "volume": None,
            }
        )
        # Bar 23: close back above the prior 20-day high (max of bars[3:23]).
        bars.append(
            {
                "time": "t23",
                "open": 1.12,
                "high": 1.18,
                "low": 1.11,
                "close": 1.17,
                "volume": None,
            }
        )
        state = lien_geometry.breakout_20_state(bars)
        self.assertTrue(state["rebreak"], state)
        self.assertEqual(state["side"], "long")
        self.assertGreaterEqual(state["pullback_bars"], 2)

    def test_snapshot_includes_new_keys(self) -> None:
        snap = indicators.snapshot(_trend_bars(80))
        self.assertIn("ma_perfect_order_age", snap)
        self.assertIn("prior_day", snap)
        self.assertIn("breakout_20", snap)
        self.assertIn("last_high", snap)
        self.assertIn("last_low", snap)


if __name__ == "__main__":
    unittest.main()
