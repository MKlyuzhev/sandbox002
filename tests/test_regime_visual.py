"""Unit tests for app.regime_visual (no network)."""

from __future__ import annotations

import unittest

from app import regime, regime_visual
from tests.test_indicators import _range_bars, _trend_bars


def _rising_swing_bars(n: int = 80, start: float = 1.0, step: float = 0.01) -> list[dict]:
    """Uptrend with periodic pullbacks so fractal swing lows exist."""
    bars: list[dict] = []
    pullback = step * 5
    for i in range(n):
        trend = start + step * i
        cycle = i % 10
        if cycle <= 6:
            close = trend
            high = close + step * 0.4
            low = close - step * 0.2
        else:
            close = trend - pullback
            high = close + step * 0.2
            low = close - step * 0.6
        bars.append(
            {
                "time": f"t{i}",
                "open": close,
                "high": high,
                "low": low,
                "close": close,
                "volume": None,
            }
        )
    return bars


class TestChannelGeometry(unittest.TestCase):
    def test_uptrend_sloped_support_and_parallel_above(self) -> None:
        bars = _rising_swing_bars(120)
        visual = regime_visual.channel_geometry(
            {"regime": "trend", "direction": "up"},
            bars,
        )
        self.assertIsNotNone(visual)
        assert visual is not None
        self.assertEqual(visual["kind"], "trend_channel")
        self.assertEqual(visual["cite"]["source"], "lien-fx")
        self.assertIn(75, visual["cite"]["chunks"])
        self.assertIn(91, visual["cite"]["chunks"])
        self.assertIn("not a Ch.15 entry", visual["notes"])
        self.assertNotIn("fallback", visual["notes"])
        lower, upper = visual["lower"], visual["upper"]
        self.assertGreater(lower["p2"], lower["p1"])
        self.assertGreater(upper["p2"], upper["p1"])
        self.assertGreater(upper["p1"], lower["p1"])
        self.assertGreater(upper["p2"], lower["p2"])

    def test_range_is_near_horizontal_10bar_box(self) -> None:
        bars = _range_bars(80, amp=0.0003)
        analysis = regime.analyze_bars(bars)
        self.assertEqual(analysis["regime"], "range")
        visual = analysis["visual"]
        self.assertEqual(visual["kind"], "range_channel")
        lower, upper = visual["lower"], visual["upper"]
        self.assertEqual(lower["i2"] - lower["i1"], 9)
        self.assertEqual(upper["i1"], lower["i1"])
        self.assertEqual(upper["i2"], lower["i2"])
        self.assertAlmostEqual(upper["p1"], upper["p2"])
        self.assertAlmostEqual(lower["p1"], lower["p2"])
        self.assertGreater(upper["p1"], lower["p1"])
        window = bars[-10:]
        self.assertAlmostEqual(upper["p1"], max(b["high"] for b in window))
        self.assertAlmostEqual(lower["p1"], min(b["low"] for b in window))

    def test_thin_swings_fall_back_to_10bar_box(self) -> None:
        bars = _trend_bars(80, step=0.01)
        visual = regime_visual.channel_geometry(
            {"regime": "trend", "direction": "up"},
            bars,
        )
        self.assertIsNotNone(visual)
        assert visual is not None
        self.assertEqual(visual["kind"], "trend_channel")
        self.assertIn("fallback", visual["notes"])
        self.assertAlmostEqual(visual["upper"]["p1"], visual["upper"]["p2"])


if __name__ == "__main__":
    unittest.main()
