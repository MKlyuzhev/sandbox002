"""Unit tests for the Ch.14 20-day breakout entry engine (no network)."""

from __future__ import annotations

import unittest

from agent.engines import breakout20
from app import risk as risk_lib


def _b20(
    *,
    rebreak: bool,
    side: str | None,
    high_20: float = 1.150,
    low_20: float = 1.050,
    pullback_bars: int = 2,
    extreme_bars_ago: int = 3,
) -> dict:
    return {
        "period": 20,
        "high_20": high_20,
        "low_20": low_20,
        "side": side,
        "extreme_bars_ago": extreme_bars_ago,
        "pullback_bars": pullback_bars,
        "rebreak": rebreak,
    }


def _analysis(
    plays: list[str],
    b20: dict,
    *,
    last_close: float = 1.160,
    waning: bool = False,
    confidence: float = 0.6,
) -> dict:
    return {
        "granularity": "D",
        "regime": "trend",
        "direction": "up" if b20.get("side") == "long" else "down",
        "trend_waning": waning,
        "allowed_play_classes": plays,
        "confidence": confidence,
        "last_close": last_close,
        "snapshot": {"last_close": last_close, "breakout_20": b20},
    }


class TestBreakout20Signal(unittest.TestCase):
    def test_rebreak_long(self) -> None:
        result = breakout20.breakout20_signal(
            _analysis(["join_trend"], _b20(rebreak=True, side="long")),
            "EUR_USD",
        )
        self.assertEqual(result["signal"], "long")
        self.assertEqual(result["play_class"], "join_trend")
        ticket = result["ticket"]
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["stop_name"], "high_20")
        self.assertLess(ticket["stop"], ticket["entry"])
        self.assertGreaterEqual(
            risk_lib.r_multiple(ticket["entry"], ticket["stop"], ticket["target"]),
            2.0,
        )
        self.assertEqual(result["chapter"], 14)
        self.assertTrue(any(c["source"] == "lien-fx" for c in result["citations"]))

    def test_rebreak_short(self) -> None:
        result = breakout20.breakout20_signal(
            _analysis(
                ["join_trend"],
                _b20(rebreak=True, side="short"),
                last_close=1.040,
            ),
            "EUR_USD",
        )
        self.assertEqual(result["signal"], "short")
        ticket = result["ticket"]
        assert ticket is not None
        self.assertEqual(ticket["stop_name"], "low_20")
        self.assertGreater(ticket["stop"], ticket["entry"])
        self.assertGreaterEqual(
            risk_lib.r_multiple(ticket["entry"], ticket["stop"], ticket["target"]),
            2.0,
        )

    def test_first_touch_is_none(self) -> None:
        result = breakout20.breakout20_signal(
            _analysis(
                ["join_trend"],
                _b20(rebreak=False, side="long", pullback_bars=0, extreme_bars_ago=0),
            ),
            "EUR_USD",
        )
        self.assertEqual(result["signal"], "none")
        self.assertIn("first touch", result["reason"])

    def test_no_setup_is_none(self) -> None:
        result = breakout20.breakout20_signal(
            _analysis(["join_trend"], _b20(rebreak=False, side=None)),
            "EUR_USD",
        )
        self.assertEqual(result["signal"], "none")

    def test_play_class_gate(self) -> None:
        result = breakout20.breakout20_signal(
            _analysis(["fade_range"], _b20(rebreak=True, side="long")),
            "EUR_USD",
        )
        self.assertEqual(result["signal"], "none")
        self.assertIn("regime allows", result["reason"])

    def test_trend_waning_is_none(self) -> None:
        result = breakout20.breakout20_signal(
            _analysis(["join_trend"], _b20(rebreak=True, side="long"), waning=True),
            "EUR_USD",
        )
        self.assertEqual(result["signal"], "none")
        self.assertIn("trend_waning", result["reason"])


class TestBreakout20Engine(unittest.TestCase):
    def test_confidence_positive_on_fire(self) -> None:
        analysis = _analysis(["join_trend"], _b20(rebreak=True, side="long"))
        b20 = analysis["snapshot"]["breakout_20"]
        conf = breakout20.signal_confidence("long", b20, 1.160, analysis)
        self.assertGreater(conf, 0.0)

    def test_confidence_zero_when_not_firing(self) -> None:
        analysis = _analysis(["join_trend"], _b20(rebreak=False, side=None))
        b20 = analysis["snapshot"]["breakout_20"]
        self.assertEqual(breakout20.signal_confidence("none", b20, 1.10, analysis), 0.0)


if __name__ == "__main__":
    unittest.main()
