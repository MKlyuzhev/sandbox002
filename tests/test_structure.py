"""Unit tests for app.structure (no network)."""

from __future__ import annotations

import unittest

from app import regime_visual, structure


def _bar(i: int, o: float, h: float, l: float, c: float, v: int = 1000) -> dict:
    return {
        "time": f"2024-02-{i + 1:02d}T00:00:00Z",
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
        "complete": True,
    }


def reversal_bars() -> list[dict]:
    """~40-bar HH/HL uptrend that rolls over into a decline (proven fixture)."""
    bars: list[dict] = []
    i = 0
    for _ in range(3):
        bars.append(_bar(i, 1.10, 1.101, 1.099, 1.10))
        i += 1
    seq = [
        (1.10, 1.11, 1.099, 1.108),
        (1.108, 1.112, 1.104, 1.106),
        (1.106, 1.107, 1.102, 1.103),
        (1.103, 1.118, 1.103, 1.116),
        (1.116, 1.120, 1.112, 1.114),
        (1.114, 1.115, 1.108, 1.109),
        (1.109, 1.126, 1.109, 1.124),
        (1.124, 1.128, 1.120, 1.122),
        (1.122, 1.123, 1.116, 1.117),
    ]
    for o, h, l, c in seq:
        bars.append(_bar(i, o, h, l, c))
        i += 1
    bars.append(_bar(i, 1.117, 1.121, 1.114, 1.115))
    i += 1
    bars.append(_bar(i, 1.116, 1.117, 1.108, 1.110))
    i += 1
    bars.append(_bar(i, 1.112, 1.113, 1.101, 1.103, 2500))
    i += 1
    bars.append(_bar(i, 1.104, 1.105, 1.098, 1.100, 2600))
    i += 1
    while len(bars) < 40:
        c = bars[-1]["close"] - 0.0005
        bars.append(_bar(i, c + 0.0004, c + 0.0006, c - 0.0004, c, 1500))
        i += 1
    return bars


def zigzag(prices: list[float], hw: float = 0.0006) -> list[dict]:
    out: list[dict] = []
    for k, c in enumerate(prices):
        o = prices[k - 1] if k > 0 else c
        out.append(_bar(k, o, max(o, c) + hw, min(o, c) - hw, c))
    return out


class TestPipSize(unittest.TestCase):
    def test_jpy_vs_other(self) -> None:
        self.assertEqual(structure.pip_size("USD_JPY"), 0.01)
        self.assertEqual(structure.pip_size("EUR_USD"), 0.0001)
        self.assertEqual(structure.pip_size(None), 0.0001)


class TestHorizontalLevels(unittest.TestCase):
    def test_keys_and_sources(self) -> None:
        hl = structure.horizontal_levels(reversal_bars(), instrument="EUR_USD")
        for key in ("levels", "nearest_support", "nearest_resistance", "atr", "pip"):
            self.assertIn(key, hl)
        self.assertTrue(hl["levels"])
        sources: set[str] = set()
        for lvl in hl["levels"]:
            sources |= set(lvl["sources"])
            self.assertIn(lvl["role"], ("support", "resistance", "at_price"))
        # Figures and prior-bar levels augment the clustered swings.
        self.assertIn("figure", sources)
        self.assertTrue({"prior_bar_high", "prior_bar_low"} & sources)


class TestTrendlineBreak(unittest.TestCase):
    def test_valid_down_break(self) -> None:
        tb = structure.trendline_break(reversal_bars(), instrument="EUR_USD")
        self.assertGreaterEqual(tb["valid_count"], 1)
        latest = tb["latest_valid"]
        self.assertIsNotNone(latest)
        self.assertEqual(latest["break_direction"], "down")
        self.assertGreaterEqual(latest["bars_beyond"], structure.TIME_FILTER)
        self.assertTrue(latest["time_filter_ok"])


class TestRoleReversal(unittest.TestCase):
    def test_detects_events(self) -> None:
        rr = structure.role_reversal(reversal_bars(), instrument="EUR_USD")
        self.assertGreaterEqual(rr["count"], 1)
        ev = rr["latest"]
        self.assertIn(ev["from_role"], ("support", "resistance"))
        self.assertIn(ev["to_role"], ("support", "resistance"))
        self.assertNotEqual(ev["from_role"], ev["to_role"])
        self.assertIn(ev["direction"], ("up", "down"))


def channel_rally_bars(last_high: float) -> list[dict]:
    """Rising channel of five up-legs; ``last_high`` sets the final rally's peak.

    1.1330 tags the return line, 1.1260 falls well short of it.
    """
    lows = [1.1000, 1.1050, 1.1100, 1.1150, 1.1200]
    highs = [1.1120, 1.1170, 1.1220, 1.1270, last_high]
    bars: list[dict] = []
    i = 0
    for k, (lo, hi) in enumerate(zip(lows, highs)):
        for s in range(4):
            c = lo + (hi - lo) * (s + 1) / 4
            bars.append(_bar(i, c - 0.0008, c + 0.0005, c - 0.0010, c))
            i += 1
        nxt = lows[k + 1] if k + 1 < len(lows) else lo + 0.0050
        for s in range(4):
            c = hi + (nxt - hi) * (s + 1) / 4
            bars.append(_bar(i, c + 0.0008, c + 0.0010, c - 0.0005, c))
            i += 1
    return bars


class TestChannel(unittest.TestCase):
    def test_has_channel_with_direction(self) -> None:
        ch = structure.channel_state(reversal_bars(), "up", instrument="EUR_USD")
        self.assertTrue(ch["has_channel"])
        for key in ("upper_at_last", "lower_at_last", "width", "far_rail_failure"):
            self.assertIn(key, ch)

    def test_no_channel_without_direction(self) -> None:
        ch = structure.channel_state(reversal_bars(), None)
        self.assertFalse(ch["has_channel"])

    def test_far_rail_failure_needs_a_failed_leg(self) -> None:
        """A rally that falls short of a previously tagged rail is the warning."""
        ch = structure.channel_state(
            channel_rally_bars(1.1260), "up", instrument="EUR_USD"
        )
        self.assertTrue(ch["far_rail_failure"])
        ev = ch["far_rail_failure_event"]
        self.assertGreaterEqual(ev["gap_frac"], structure.FAR_RAIL_FAIL_FRAC)
        self.assertLessEqual(ev["prior_reach_gap_frac"], structure.FAR_RAIL_REACH_FRAC)
        self.assertLess(ev["prior_reach_index"], ev["index"])
        self.assertGreater(ev["bars_since_reach"], 0)

    def test_rally_that_tags_the_rail_is_not_a_failure(self) -> None:
        ch = structure.channel_state(
            channel_rally_bars(1.1330), "up", instrument="EUR_USD"
        )
        self.assertFalse(ch["far_rail_failure"])
        self.assertIsNone(ch["far_rail_failure_event"])
        self.assertIsNone(ch["far_rail_gap_frac"])

    def test_far_rail_failure_requires_a_prior_reach(self) -> None:
        """Without an earlier leg that tagged the rail there is nothing to fail."""
        bars = channel_rally_bars(1.1260)
        rails = regime_visual._trend_channel(bars, "up")
        assert rails is not None
        upper, lower = rails
        width = float(upper["p2"]) - float(lower["p2"])
        unreachable = structure._far_rail_failure_event(
            bars,
            upper,
            "up",
            width,
            swing_left=structure.SWING_LEFT,
            swing_right=structure.SWING_RIGHT,
            fail_frac=structure.FAR_RAIL_FAIL_FRAC,
            reach_frac=-1.0,
        )
        self.assertIsNone(unreachable)

    def test_far_rail_failure_requires_a_shortfall(self) -> None:
        bars = channel_rally_bars(1.1260)
        rails = regime_visual._trend_channel(bars, "up")
        assert rails is not None
        upper, lower = rails
        width = float(upper["p2"]) - float(lower["p2"])
        no_fail = structure._far_rail_failure_event(
            bars,
            upper,
            "up",
            width,
            swing_left=structure.SWING_LEFT,
            swing_right=structure.SWING_RIGHT,
            fail_frac=0.99,
            reach_frac=structure.FAR_RAIL_REACH_FRAC,
        )
        self.assertIsNone(no_fail)

    def test_far_rail_failure_ignores_legs_outside_the_channel(self) -> None:
        """A gap of a full width means the channel already broke, not a warning."""
        bars = channel_rally_bars(1.1260)
        rails = regime_visual._trend_channel(bars, "up")
        assert rails is not None
        upper, lower = rails
        width = float(upper["p2"]) - float(lower["p2"])
        # Shrinking the declared width inflates every gap_frac past 1.0.
        outside = structure._far_rail_failure_event(
            bars,
            upper,
            "up",
            width / 10.0,
            swing_left=structure.SWING_LEFT,
            swing_right=structure.SWING_RIGHT,
            fail_frac=structure.FAR_RAIL_FAIL_FRAC,
            reach_frac=structure.FAR_RAIL_REACH_FRAC,
        )
        self.assertIsNone(outside)

    def test_rail_price_interpolates(self) -> None:
        rail = {"i1": 0, "p1": 1.1000, "i2": 10, "p2": 1.1100}
        self.assertAlmostEqual(structure._rail_price_at(rail, 0), 1.1000)
        self.assertAlmostEqual(structure._rail_price_at(rail, 5), 1.1050)
        self.assertAlmostEqual(structure._rail_price_at(rail, 10), 1.1100)
        flat = {"i1": 3, "p1": 1.2, "i2": 3, "p2": 1.2}
        self.assertAlmostEqual(structure._rail_price_at(flat, 99), 1.2)


class TestFan(unittest.TestCase):
    def test_keys(self) -> None:
        fan = structure.fan_state(reversal_bars(), "up", instrument="EUR_USD")
        for key in ("broken_count", "first_line_broken", "third_line_broken"):
            self.assertIn(key, fan)


class TestSwingFlip(unittest.TestCase):
    def test_up_then_flip(self) -> None:
        prices = [
            1.100, 1.104, 1.108, 1.105, 1.102, 1.106, 1.112, 1.109, 1.106,
            1.110, 1.118, 1.114, 1.110, 1.114, 1.116, 1.112, 1.108, 1.104, 1.100,
        ]
        flip = structure.swing_structure_flip(zigzag(prices), swing_left=2, swing_right=2)
        self.assertEqual(flip["prior_trend"], "up")
        self.assertTrue(flip["failed_new_extreme"])
        self.assertTrue(flip["broke_prior_swing"])
        self.assertTrue(flip["flip"])
        self.assertEqual(flip["direction_to"], "down")

    def test_flat_no_flip(self) -> None:
        flat = [_bar(i, 1.1, 1.1005, 1.0995, 1.1) for i in range(30)]
        flip = structure.swing_structure_flip(flat)
        self.assertFalse(flip["flip"])


class TestAnalyzeStructure(unittest.TestCase):
    def test_shape(self) -> None:
        st = structure.analyze_structure(reversal_bars(), "up", instrument="EUR_USD")
        for key in (
            "levels",
            "role_reversal",
            "trendline_break",
            "channel",
            "fan",
            "swing_flip",
        ):
            self.assertIn(key, st)


class TestErrors(unittest.TestCase):
    def test_empty_raises(self) -> None:
        with self.assertRaises(structure.StructureError):
            structure.horizontal_levels([])

    def test_high_below_low_raises(self) -> None:
        bad = [{"open": 1.0, "high": 0.9, "low": 1.0, "close": 1.0}]
        with self.assertRaises(structure.StructureError):
            structure.trendline_break(bad)


if __name__ == "__main__":
    unittest.main()
