"""Unit tests for REST-shaped walk fills (no network)."""

from __future__ import annotations

import math
import unittest

from agent.walk_exec import (
    ba_ohlc,
    check_exit_rest,
    fill_through_stop,
    may_check_exit,
    rest_fill_price,
    rest_journal_note,
    rest_pnl,
    rest_units,
    try_rest_fill,
    window_end_price,
)
from app.regime_walk import WalkError
from app.risk import position_size, r_multiple


def _ba_bar(
    *,
    mid_o: float = 1.10,
    mid_h: float = 1.12,
    mid_l: float = 1.08,
    mid_c: float = 1.11,
    half: float = 0.0002,
) -> dict:
    return {
        "open": mid_o,
        "high": mid_h,
        "low": mid_l,
        "close": mid_c,
        "bid": {
            "o": mid_o - half,
            "h": mid_h - half,
            "l": mid_l - half,
            "c": mid_c - half,
        },
        "ask": {
            "o": mid_o + half,
            "h": mid_h + half,
            "l": mid_l + half,
            "c": mid_c + half,
        },
        "complete": True,
    }


class TestRestFill(unittest.TestCase):
    def test_long_fill_is_ask_open(self) -> None:
        bar = _ba_bar(mid_o=1.2000, half=0.0002)
        self.assertAlmostEqual(rest_fill_price("long", bar), 1.2002)

    def test_short_fill_is_bid_open(self) -> None:
        bar = _ba_bar(mid_o=1.2000, half=0.0002)
        self.assertAlmostEqual(rest_fill_price("short", bar), 1.1998)

    def test_skip_if_long_fill_through_stop(self) -> None:
        bar = _ba_bar(mid_o=1.08, half=0.0002)
        self.assertTrue(fill_through_stop("long", rest_fill_price("long", bar), 1.09))
        self.assertIsNone(
            try_rest_fill("long", 1.09, bar, 10_000.0, 0.02, 1.0)
        )

    def test_skip_if_short_fill_through_stop(self) -> None:
        bar = _ba_bar(mid_o=1.12, half=0.0002)
        self.assertIsNone(
            try_rest_fill("short", 1.10, bar, 10_000.0, 0.02, 1.0)
        )

    def test_units_and_pnl_match_position_size(self) -> None:
        fill = 1.1002
        stop = 1.0900
        exit_px = 1.0900
        units = rest_units(10_000.0, 0.02, fill, stop, 1.0)
        expected = math.floor(position_size(10_000.0, 0.02, abs(fill - stop), 1.0))
        self.assertEqual(units, expected)
        self.assertGreaterEqual(units, 1)
        pnl = rest_pnl("long", fill, exit_px, units, 1.0)
        self.assertAlmostEqual(pnl, units * (exit_px - fill))
        self.assertAlmostEqual(r_multiple(fill, stop, exit_px), -1.0)

    def test_units_below_one_skips(self) -> None:
        # Tiny equity vs a wide stop.
        self.assertIsNone(rest_units(1.0, 0.02, 1.20, 1.00, 1.0))

    def test_missing_ba_raises(self) -> None:
        bar = {"open": 1.1, "high": 1.2, "low": 1.0, "close": 1.1}
        with self.assertRaises(WalkError):
            ba_ohlc(bar, "ask")
        with self.assertRaises(WalkError):
            rest_fill_price("long", bar)

    def test_journal_note(self) -> None:
        self.assertEqual(
            rest_journal_note("long", 12345),
            "walk fill rest next-open ask; units=12345",
        )
        self.assertEqual(
            rest_journal_note("short", 9),
            "walk fill rest next-open bid; units=9",
        )


class TestRestExit(unittest.TestCase):
    def test_gap_at_open_exits_at_open_not_stop(self) -> None:
        bar = _ba_bar(mid_o=1.07, mid_h=1.08, mid_l=1.06, mid_c=1.07, half=0.0002)
        hit = check_exit_rest("long", stop=1.09, target=1.14, bar=bar)
        self.assertEqual(hit[0], "stop")
        self.assertAlmostEqual(hit[1], bar["bid"]["o"])
        self.assertNotAlmostEqual(hit[1], 1.09)

    def test_long_stop_uses_bid_not_mid(self) -> None:
        # Mid low stays above stop; bid low trades through.
        bar = _ba_bar(mid_o=1.12, mid_h=1.13, mid_l=1.101, mid_c=1.11, half=0.002)
        self.assertGreater(bar["low"], 1.10)
        self.assertLessEqual(bar["bid"]["l"], 1.10)
        hit = check_exit_rest("long", stop=1.10, target=1.20, bar=bar)
        self.assertEqual(hit, ("stop", 1.10))

    def test_short_stop_uses_ask(self) -> None:
        bar = _ba_bar(mid_o=1.10, mid_h=1.109, mid_l=1.09, mid_c=1.10, half=0.002)
        hit = check_exit_rest("short", stop=1.11, target=1.05, bar=bar)
        self.assertEqual(hit, ("stop", 1.11))

    def test_stop_wins_when_both_trade_after_open(self) -> None:
        bar = _ba_bar(mid_o=1.12, mid_h=1.30, mid_l=1.05, mid_c=1.15, half=0.0002)
        hit = check_exit_rest("long", stop=1.10, target=1.20, bar=bar)
        self.assertEqual(hit, ("stop", 1.10))

    def test_same_bar_as_fill_allowed(self) -> None:
        self.assertTrue(may_check_exit(10, 10, "rest"))
        self.assertFalse(may_check_exit(10, 10, "close"))
        self.assertTrue(may_check_exit(10, 11, "close"))

    def test_window_end_making_close(self) -> None:
        bar = _ba_bar(mid_c=1.111, half=0.0002)
        self.assertAlmostEqual(window_end_price(bar, "long", "rest"), bar["bid"]["c"])
        self.assertAlmostEqual(window_end_price(bar, "short", "rest"), bar["ask"]["c"])
        self.assertAlmostEqual(window_end_price(bar, "long", "close"), bar["close"])


if __name__ == "__main__":
    unittest.main()
