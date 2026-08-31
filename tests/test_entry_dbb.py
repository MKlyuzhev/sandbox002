"""Unit tests for the Ch.9 Double Bollinger Bands entry engine (no network).

The engine is pure over ``regime.analyze_bars``-shaped dicts, so tests pass
hand-built analysis dicts (with a ``double_bb`` snapshot block) for
deterministic coverage of the 1sigma-cross triggers.
"""

from __future__ import annotations

import unittest

from agent.engines import dbb
from app import risk as risk_lib


def _dbb_block(
    zone: str,
    prev_zone: str,
    prev2_zone: str,
    *,
    mid: float = 1.100,
    upper_1: float = 1.105,
    upper_2: float = 1.110,
    lower_1: float = 1.095,
    lower_2: float = 1.090,
) -> dict:
    return {
        "period": 20,
        "upper_2": upper_2,
        "upper_1": upper_1,
        "mid": mid,
        "lower_1": lower_1,
        "lower_2": lower_2,
        "zone": zone,
        "prev_zone": prev_zone,
        "prev2_zone": prev2_zone,
    }


def _analysis(
    plays: list[str],
    dbb_block: dict,
    *,
    regime: str = "trend",
    direction: str | None = "up",
    waning: bool = False,
    last_close: float = 1.107,
    confidence: float = 0.6,
) -> dict:
    return {
        "granularity": "D",
        "regime": regime,
        "direction": direction,
        "trend_waning": waning,
        "allowed_play_classes": plays,
        "confidence": confidence,
        "last_close": last_close,
        "snapshot": {"last_close": last_close, "double_bb": dbb_block},
    }


class TestDbbSignal(unittest.TestCase):
    def test_join_long_breaks_upper_band(self) -> None:
        analysis = _analysis(
            ["join_trend"],
            _dbb_block("trend_up", "range", "range"),
            last_close=1.107,
        )
        result = dbb.dbb_signal(analysis, "EUR_USD")
        self.assertEqual(result["signal"], "long")
        self.assertEqual(result["play_class"], "join_trend")
        ticket = result["ticket"]
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["side"], "long")
        self.assertLess(ticket["stop"], ticket["entry"])
        self.assertEqual(ticket["stop_name"], "bb_mid")
        self.assertGreaterEqual(
            risk_lib.r_multiple(ticket["entry"], ticket["stop"], ticket["target"]),
            2.0,
        )
        self.assertEqual(result["chapter"], 9)
        self.assertTrue(any(c["source"] == "lien-fx" for c in result["citations"]))

    def test_join_short_breaks_lower_band(self) -> None:
        analysis = _analysis(
            ["join_trend"],
            _dbb_block("trend_down", "range", "range"),
            regime="trend",
            direction="down",
            last_close=1.093,
        )
        result = dbb.dbb_signal(analysis, "EUR_USD")
        self.assertEqual(result["signal"], "short")
        self.assertEqual(result["play_class"], "join_trend")
        ticket = result["ticket"]
        assert ticket is not None
        self.assertGreater(ticket["stop"], ticket["entry"])
        self.assertGreaterEqual(
            risk_lib.r_multiple(ticket["entry"], ticket["stop"], ticket["target"]),
            2.0,
        )

    def test_fade_long_reclaims_lower_band(self) -> None:
        analysis = _analysis(
            ["fade_range"],
            _dbb_block("range", "trend_down", "trend_down"),
            regime="range",
            direction=None,
            last_close=1.096,
        )
        result = dbb.dbb_signal(analysis, "EUR_USD")
        self.assertEqual(result["signal"], "long")
        self.assertEqual(result["play_class"], "fade_range")
        ticket = result["ticket"]
        assert ticket is not None
        self.assertEqual(ticket["stop_name"], "bb_lower_1")
        self.assertLess(ticket["stop"], ticket["entry"])

    def test_fade_short_loses_upper_band(self) -> None:
        analysis = _analysis(
            ["fade_range"],
            _dbb_block("range", "trend_up", "trend_up"),
            regime="range",
            direction=None,
            last_close=1.104,
        )
        result = dbb.dbb_signal(analysis, "EUR_USD")
        self.assertEqual(result["signal"], "short")
        self.assertEqual(result["play_class"], "fade_range")
        ticket = result["ticket"]
        assert ticket is not None
        self.assertEqual(ticket["stop_name"], "bb_upper_1")
        self.assertGreater(ticket["stop"], ticket["entry"])

    def test_no_cross_is_none(self) -> None:
        analysis = _analysis(
            ["fade_range"],
            _dbb_block("range", "range", "range"),
            regime="range",
            direction=None,
        )
        result = dbb.dbb_signal(analysis, "EUR_USD")
        self.assertEqual(result["signal"], "none")
        self.assertIsNone(result["ticket"])

    def test_join_break_but_already_in_zone_is_none(self) -> None:
        # Prior bar already in the trend zone -> not a fresh break.
        analysis = _analysis(
            ["join_trend"],
            _dbb_block("trend_up", "trend_up", "range"),
        )
        result = dbb.dbb_signal(analysis, "EUR_USD")
        self.assertEqual(result["signal"], "none")

    def test_play_class_gate_blocks_wrong_regime(self) -> None:
        # A join breakout while the regime only allows fade_range -> no signal.
        analysis = _analysis(
            ["fade_range"],
            _dbb_block("trend_up", "range", "range"),
            regime="range",
            direction=None,
        )
        result = dbb.dbb_signal(analysis, "EUR_USD")
        self.assertEqual(result["signal"], "none")
        self.assertIn("regime allows", result["reason"])

    def test_trend_waning_is_none(self) -> None:
        analysis = _analysis(
            ["join_trend"],
            _dbb_block("trend_up", "range", "range"),
            waning=True,
        )
        result = dbb.dbb_signal(analysis, "EUR_USD")
        self.assertEqual(result["signal"], "none")
        self.assertIn("trend_waning", result["reason"])


class TestDbbEngine(unittest.TestCase):
    def test_confidence_positive_on_fire(self) -> None:
        analysis = _analysis(
            ["join_trend"],
            _dbb_block("trend_up", "range", "range"),
            last_close=1.109,
        )
        dbb_block = analysis["snapshot"]["double_bb"]
        conf = dbb.signal_confidence("long", "join_trend", dbb_block, 1.109, analysis)
        self.assertGreater(conf, 0.0)

    def test_confidence_zero_when_not_firing(self) -> None:
        analysis = _analysis(["join_trend"], _dbb_block("range", "range", "range"))
        dbb_block = analysis["snapshot"]["double_bb"]
        conf = dbb.signal_confidence("none", "breakout_watch", dbb_block, 1.10, analysis)
        self.assertEqual(conf, 0.0)


if __name__ == "__main__":
    unittest.main()
