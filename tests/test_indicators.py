"""Unit tests for app.indicators (no network)."""

from __future__ import annotations

import unittest

from app import indicators


def _bar(i: int, close: float, half: float = 0.001) -> dict:
    return {
        "time": f"t{i}",
        "open": close,
        "high": close + half,
        "low": close - half,
        "close": close,
        "volume": None,
    }


def _trend_bars(n: int, start: float = 1.0, step: float = 0.01) -> list[dict]:
    bars = []
    p = start
    for i in range(n):
        p += step
        bars.append(_bar(i, p, half=abs(step) * 0.3 + 0.0005))
    return bars


def _range_bars(n: int, level: float = 1.2, amp: float = 0.0008) -> list[dict]:
    bars = []
    for i in range(n):
        c = level + (amp if i % 2 == 0 else -amp)
        bars.append(_bar(i, c, half=amp * 0.5))
    return bars


class TestSma(unittest.TestCase):
    def test_warmup_then_mean(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        s = indicators.sma_series(values, 3)
        self.assertIsNone(s[0])
        self.assertIsNone(s[1])
        self.assertAlmostEqual(s[2], 2.0)
        self.assertAlmostEqual(s[4], 4.0)


class TestBollinger(unittest.TestCase):
    def test_width_contracts_on_flat(self) -> None:
        trend = _trend_bars(80, step=0.02)
        flat = _range_bars(80, amp=0.0003)
        t_snap = indicators.snapshot(trend)
        f_snap = indicators.snapshot(flat)
        t_w = t_snap["bollinger"]["width"]
        f_w = f_snap["bollinger"]["width"]
        self.assertIsNotNone(t_w)
        self.assertIsNotNone(f_w)
        self.assertGreater(t_w, f_w)

    def test_uptrend_zone_is_trend_up(self) -> None:
        bars = _trend_bars(80, step=0.03)
        snap = indicators.snapshot(bars)
        self.assertEqual(snap["bollinger"]["zone"], "trend_up")


class TestDoubleBollinger(unittest.TestCase):
    def test_state_keys_and_zone(self) -> None:
        closes = [1.0 + 0.01 * i for i in range(60)]
        dbb = indicators.double_bollinger_state(closes)
        for key in (
            "upper_2",
            "upper_1",
            "mid",
            "lower_1",
            "lower_2",
            "zone",
            "prev_zone",
            "prev2_zone",
        ):
            self.assertIn(key, dbb)
        # A steady climb keeps the close above the upper 1sigma band.
        self.assertEqual(dbb["zone"], "trend_up")

    def test_snapshot_carries_double_bb(self) -> None:
        bars = _trend_bars(80, step=0.03)
        snap = indicators.snapshot(bars)
        self.assertIn("double_bb", snap)
        self.assertEqual(snap["double_bb"]["zone"], snap["bollinger"]["zone"])

    def test_cross_up_from_range_to_trend(self) -> None:
        # Flat closes (range) then a jump that closes above the upper band.
        closes = [1.20] * 30 + [1.24]
        dbb = indicators.double_bollinger_state(closes)
        self.assertEqual(dbb["zone"], "trend_up")
        self.assertEqual(dbb["prev_zone"], "range")
        self.assertEqual(dbb["prev2_zone"], "range")


class TestAdx(unittest.TestCase):
    def test_adx_higher_on_directional_run(self) -> None:
        trend = _trend_bars(80, step=0.015)
        flat = _range_bars(80, amp=0.0004)
        t_adx = indicators.snapshot(trend)["adx"]["adx"]
        f_adx = indicators.snapshot(flat)["adx"]["adx"]
        self.assertIsNotNone(t_adx)
        self.assertIsNotNone(f_adx)
        self.assertGreater(t_adx, 25.0)
        self.assertGreater(t_adx, f_adx)

    def test_adx_rising_on_fresh_trend(self) -> None:
        bars = _range_bars(40, amp=0.0005) + _trend_bars(50, start=1.2, step=0.012)
        # Re-index times
        for i, b in enumerate(bars):
            b["time"] = f"t{i}"
        snap = indicators.snapshot(bars)
        self.assertTrue(snap["adx"]["rising"])
        self.assertGreater(snap["adx"]["adx"], 20.0)


class TestPerfectOrder(unittest.TestCase):
    def test_stacked_on_long_uptrend(self) -> None:
        bars = _trend_bars(250, step=0.005)
        snap = indicators.snapshot(bars)
        self.assertEqual(snap["ma_perfect_order"], "up")
        self.assertEqual(snap["sma_missing"], [])
        self.assertEqual(snap["close_vs_sma"]["200"], "above")

    def test_none_when_200_missing(self) -> None:
        bars = _trend_bars(80, step=0.01)
        snap = indicators.snapshot(bars)
        self.assertIn(200, snap["sma_missing"])
        self.assertIsNone(snap["ma_perfect_order"])


class TestOscillators(unittest.TestCase):
    def test_rsi_high_on_uptrend(self) -> None:
        bars = _trend_bars(60, step=0.01)
        snap = indicators.snapshot(bars)
        self.assertIsNotNone(snap["rsi"])
        self.assertGreater(snap["rsi"], 70.0)

    def test_macd_line_positive_on_uptrend(self) -> None:
        bars = _trend_bars(80, step=0.01)
        snap = indicators.snapshot(bars)
        self.assertIsNotNone(snap["macd"]["macd"])
        self.assertGreater(snap["macd"]["macd"], 0.0)


class TestGuards(unittest.TestCase):
    def test_too_few_bars(self) -> None:
        with self.assertRaises(indicators.IndicatorError):
            indicators.snapshot(_trend_bars(10))

    def test_unavailable_option_fields(self) -> None:
        snap = indicators.snapshot(_trend_bars(40))
        self.assertEqual(snap["risk_reversals"], "unavailable")
        self.assertEqual(snap["implied_vol"], "unavailable")


if __name__ == "__main__":
    unittest.main()
