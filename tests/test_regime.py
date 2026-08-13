"""Unit tests for app.regime (no network)."""

from __future__ import annotations

import unittest

from app import indicators, regime
from tests.test_indicators import _range_bars, _trend_bars


class TestClassifyFromLiveSnapshots(unittest.TestCase):
    def test_strong_uptrend_is_join_trend(self) -> None:
        bars = _trend_bars(250, step=0.008)
        result = regime.analyze_bars(bars)
        self.assertEqual(result["regime"], "trend")
        self.assertIn("join_trend", result["allowed_play_classes"])
        self.assertFalse(result["trend_waning"])
        self.assertGreaterEqual(result["trend_x_count"], 3)
        self.assertEqual(result["direction"], "up")
        self.assertEqual(result["snapshot"]["risk_reversals"], "unavailable")

    def test_tight_range_is_fade_range(self) -> None:
        bars = _range_bars(80, amp=0.0003)
        result = regime.analyze_bars(bars)
        self.assertEqual(result["regime"], "range")
        self.assertIn("fade_range", result["allowed_play_classes"])
        self.assertTrue(result["range_checks"]["inside_1sigma"])
        self.assertTrue(result["range_checks"]["adx_below_25"])


class TestTrendWaning(unittest.TestCase):
    def test_adx_high_and_falling(self) -> None:
        snap = indicators.snapshot(_trend_bars(80, step=0.01))
        # Overwrite ADX block: still trending by level, but rolling over.
        snap["adx"] = {
            "adx": 42.0,
            "plus_di": 30.0,
            "minus_di": 12.0,
            "slope": -4.0,
            "slope_bars": 5,
            "rising": False,
        }
        classified = regime.classify(snap)
        self.assertTrue(classified["trend_waning"])
        self.assertEqual(classified["regime"], "mixed")
        self.assertIn("breakout_watch", classified["allowed_play_classes"])
        self.assertTrue(any("trend_waning" in n for n in classified["notes"]))


class TestChecklistCounts(unittest.TestCase):
    def test_fixture_range_checks(self) -> None:
        classified = regime.classify(
            {
                "adx": {
                    "adx": 14.0,
                    "plus_di": 18.0,
                    "minus_di": 17.0,
                    "slope": -1.2,
                    "rising": False,
                },
                "bollinger": {"zone": "range"},
                "rsi": 72.0,
                "stoch": {"k": 82.0, "d": 80.0},
                "macd": {"hist": 0.0},
                "ma_perfect_order": None,
                "close_vs_sma": {"20": "at", "50": "at", "100": None},
                "sma_missing": [100, 200],
                "risk_reversals": "unavailable",
                "implied_vol": "unavailable",
            }
        )
        self.assertEqual(classified["regime"], "range")
        self.assertTrue(classified["range_checks"]["rsi_extreme"])
        self.assertTrue(classified["range_checks"]["stoch_extreme"])
        self.assertGreaterEqual(classified["range_x_count"], 3)


if __name__ == "__main__":
    unittest.main()
