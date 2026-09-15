"""Unit tests for app.candles (no network)."""

from __future__ import annotations

import unittest

from app import candles


def _b(o: float, h: float, l: float, c: float, v: int | None = 1000) -> dict:
    return {"time": "t", "open": o, "high": h, "low": l, "close": c, "volume": v, "complete": True}


class TestSingleBarPatterns(unittest.TestCase):
    def test_engulfing(self) -> None:
        bull = [_b(10.0, 10.05, 9.45, 9.5), _b(9.4, 10.15, 9.35, 10.1)]
        self.assertEqual(candles.engulfing(bull)["pattern"], "bullish_engulfing")
        bear = [_b(9.5, 10.05, 9.45, 10.0), _b(10.1, 10.15, 9.35, 9.4)]
        self.assertEqual(candles.engulfing(bear)["direction"], "down")

    def test_doji_variants(self) -> None:
        grave = candles.doji([_b(9.80, 10.20, 9.79, 9.81)])
        self.assertEqual(grave["pattern"], "gravestone_doji")
        self.assertEqual(grave["direction"], "down")
        # A wide-bodied bar is not a doji.
        self.assertIsNone(candles.doji([_b(9.0, 10.0, 9.0, 10.0)]))

    def test_hammer_and_shooting_star(self) -> None:
        self.assertEqual(
            candles.hammer_shooting_star([_b(10.0, 10.08, 9.5, 10.05)])["pattern"],
            "hammer",
        )
        self.assertEqual(
            candles.hammer_shooting_star([_b(10.0, 10.5, 9.93, 9.95)])["pattern"],
            "shooting_star",
        )

    def test_dark_cloud_and_piercing(self) -> None:
        dc = [_b(9.5, 10.05, 9.45, 10.0), _b(10.10, 10.12, 9.68, 9.70)]
        self.assertEqual(candles.dark_cloud_piercing(dc)["pattern"], "dark_cloud_cover")
        pi = [_b(10.0, 10.05, 9.45, 9.5), _b(9.40, 9.85, 9.35, 9.80)]
        self.assertEqual(candles.dark_cloud_piercing(pi)["pattern"], "piercing")

    def test_harami(self) -> None:
        bull = [_b(10.0, 10.05, 9.45, 9.5), _b(9.6, 9.95, 9.55, 9.9)]
        self.assertEqual(candles.harami(bull)["pattern"], "bullish_harami")
        bear = [_b(9.5, 10.05, 9.45, 10.0), _b(9.9, 9.95, 9.55, 9.6)]
        self.assertEqual(candles.harami(bear)["direction"], "down")

    def test_key_reversal(self) -> None:
        bull = [_b(9.75, 9.80, 9.60, 9.70), _b(9.65, 9.85, 9.50, 9.80)]
        self.assertEqual(candles.key_reversal_day(bull)["pattern"], "bullish_key_reversal")
        bear = [_b(9.90, 10.00, 9.85, 9.95), _b(9.98, 10.10, 9.80, 9.85)]
        self.assertEqual(candles.key_reversal_day(bear)["direction"], "down")

    def test_no_pattern(self) -> None:
        plain = [_b(10.0, 10.02, 9.98, 10.0), _b(10.0, 10.02, 9.98, 10.0)]
        self.assertIsNone(candles.engulfing(plain))
        self.assertIsNone(candles.harami(plain))


class TestVolumeContext(unittest.TestCase):
    def test_confirmed(self) -> None:
        bars = [_b(1, 1, 1, 1, 10) for _ in range(10)] + [_b(1, 1, 1, 1, 30)]
        vc = candles.volume_context(bars)
        self.assertEqual(vc["volume_kind"], "tick")
        self.assertTrue(vc["volume_confirmed"])

    def test_not_confirmed(self) -> None:
        bars = [_b(1, 1, 1, 1, 10) for _ in range(10)] + [_b(1, 1, 1, 1, 5)]
        self.assertFalse(candles.volume_context(bars)["volume_confirmed"])

    def test_unavailable(self) -> None:
        vc = candles.volume_context([_b(1, 1, 1, 1, None)])
        self.assertEqual(vc["volume_kind"], "unavailable")
        self.assertIsNone(vc["volume_confirmed"])


class TestAnalyzeCandles(unittest.TestCase):
    def test_net_direction_and_level(self) -> None:
        # Bullish engulfing whose low sits at a support level, with volume spike.
        history = [_b(9.5, 9.6, 9.4, 9.5, 10) for _ in range(10)]
        bars = history + [
            _b(10.0, 10.05, 9.45, 9.5, 10),
            _b(9.4, 10.15, 9.35, 10.1, 40),
        ]
        levels = {"atr": 0.2, "levels": [{"price": 9.35, "role": "support"}]}
        out = candles.analyze_candles(bars, levels=levels)
        self.assertEqual(out["reversal_direction"], "up")
        self.assertTrue(any(p["pattern"] == "bullish_engulfing" for p in out["patterns"]))
        self.assertTrue(out["any_at_level"])
        self.assertTrue(out["any_volume_confirmed"])

    def test_error_on_empty(self) -> None:
        with self.assertRaises(candles.CandleError):
            candles.analyze_candles([])


if __name__ == "__main__":
    unittest.main()
