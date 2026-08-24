"""Unit tests for the Ch.8 MTF entry engine (no network).

The engine is pure over ``regime.analyze_bars``-shaped dicts, so tests pass
hand-built higher/lower timeframe analysis dicts for deterministic coverage.
"""

from __future__ import annotations

import unittest

from agent.engines import mtf
from app import risk as risk_lib


def _htf(direction: str | None, *, waning: bool = False) -> dict:
    regime = "trend" if direction in ("up", "down") else "mixed"
    plays = ["join_trend"] if regime == "trend" else ["breakout_watch"]
    return {
        "granularity": "D",
        "regime": regime,
        "direction": direction,
        "trend_waning": waning,
        "allowed_play_classes": plays,
    }


def _ltf(rsi: float | None, *, last_close: float = 1.10, high_n: float = 1.11, low_n: float = 1.09) -> dict:
    return {
        "granularity": "H1",
        "last_close": last_close,
        "snapshot": {
            "last_close": last_close,
            "rsi": rsi,
            "high_n": high_n,
            "low_n": low_n,
            "sma": {},
            "bollinger": {},
        },
    }


class TestMtfSignal(unittest.TestCase):
    def test_htf_up_ltf_oversold_is_long(self) -> None:
        result = mtf.mtf_signal(_htf("up"), _ltf(28.0), "EUR_USD")
        self.assertEqual(result["signal"], "long")
        ticket = result["ticket"]
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["side"], "long")
        self.assertLess(ticket["stop"], ticket["entry"])
        self.assertGreaterEqual(
            risk_lib.r_multiple(ticket["entry"], ticket["stop"], ticket["target"]),
            2.0,
        )
        self.assertEqual(result["chapter"], 8)
        self.assertTrue(
            any(c["source"] == "lien-fx" for c in result["citations"])
        )

    def test_htf_down_ltf_overbought_is_short(self) -> None:
        result = mtf.mtf_signal(_htf("down"), _ltf(74.0), "EUR_USD")
        self.assertEqual(result["signal"], "short")
        ticket = result["ticket"]
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["side"], "short")
        self.assertGreater(ticket["stop"], ticket["entry"])
        self.assertGreaterEqual(
            risk_lib.r_multiple(ticket["entry"], ticket["stop"], ticket["target"]),
            2.0,
        )

    def test_htf_up_ltf_not_oversold_is_none(self) -> None:
        result = mtf.mtf_signal(_htf("up"), _ltf(55.0), "EUR_USD")
        self.assertEqual(result["signal"], "none")
        self.assertIsNone(result["ticket"])

    def test_no_htf_direction_is_none(self) -> None:
        result = mtf.mtf_signal(_htf(None), _ltf(20.0), "EUR_USD")
        self.assertEqual(result["signal"], "none")
        self.assertIn("no clear direction", result["reason"])

    def test_htf_trend_waning_is_none(self) -> None:
        result = mtf.mtf_signal(_htf("up", waning=True), _ltf(20.0), "EUR_USD")
        self.assertEqual(result["signal"], "none")
        self.assertIn("trend_waning", result["reason"])

    def test_htf_up_ltf_overbought_no_chasing(self) -> None:
        result = mtf.mtf_signal(_htf("up"), _ltf(85.0), "EUR_USD")
        self.assertEqual(result["signal"], "none")
        self.assertIsNone(result["ticket"])

    def test_htf_down_ltf_oversold_no_fading(self) -> None:
        result = mtf.mtf_signal(_htf("down"), _ltf(15.0), "EUR_USD")
        self.assertEqual(result["signal"], "none")

    def test_missing_ltf_rsi_is_none(self) -> None:
        result = mtf.mtf_signal(_htf("up"), _ltf(None), "EUR_USD")
        self.assertEqual(result["signal"], "none")
        self.assertIn("rsi unavailable", result["reason"])

    def test_custom_thresholds(self) -> None:
        # rsi 45 counts as a dip when os threshold is raised to 50
        result = mtf.mtf_signal(_htf("up"), _ltf(45.0), "EUR_USD", rsi_os=50.0)
        self.assertEqual(result["signal"], "long")


if __name__ == "__main__":
    unittest.main()
