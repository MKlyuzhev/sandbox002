"""Unit tests for the Ch.16 Perfect order entry engine (no network)."""

from __future__ import annotations

import unittest

from agent.engines import perfect_order
from app import risk as risk_lib


def _analysis(
    plays: list[str],
    *,
    order: str | None = "up",
    age: int | None = 5,
    adx: float = 25.0,
    rising: bool = True,
    waning: bool = False,
    last_close: float = 1.120,
    last_low: float = 1.100,
    last_high: float = 1.125,
    sma20: float = 1.090,
    confidence: float = 0.6,
) -> dict:
    return {
        "granularity": "D",
        "regime": "trend",
        "direction": "up" if order == "up" else "down",
        "trend_waning": waning,
        "allowed_play_classes": plays,
        "confidence": confidence,
        "last_close": last_close,
        "snapshot": {
            "last_close": last_close,
            "last_low": last_low,
            "last_high": last_high,
            "ma_perfect_order": order,
            "ma_perfect_order_age": age,
            "sma": {"20": sma20},
            "adx": {"adx": adx, "rising": rising},
        },
    }


class TestPerfectOrderSignal(unittest.TestCase):
    def test_age_five_up_stack_is_long(self) -> None:
        result = perfect_order.perfect_order_signal(_analysis(["join_trend"]), "EUR_USD")
        self.assertEqual(result["signal"], "long")
        self.assertEqual(result["play_class"], "join_trend")
        ticket = result["ticket"]
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["side"], "long")
        self.assertLess(ticket["stop"], ticket["entry"])
        self.assertGreaterEqual(
            risk_lib.r_multiple(ticket["entry"], ticket["stop"], ticket["target"]),
            2.0,
        )
        self.assertEqual(result["chapter"], 16)
        self.assertTrue(any(c["source"] == "lien-fx" for c in result["citations"]))

    def test_age_five_down_stack_is_short(self) -> None:
        result = perfect_order.perfect_order_signal(
            _analysis(
                ["join_trend"],
                order="down",
                last_close=1.080,
                last_high=1.100,
                sma20=1.110,
            ),
            "EUR_USD",
        )
        self.assertEqual(result["signal"], "short")
        ticket = result["ticket"]
        assert ticket is not None
        self.assertGreater(ticket["stop"], ticket["entry"])
        self.assertGreaterEqual(
            risk_lib.r_multiple(ticket["entry"], ticket["stop"], ticket["target"]),
            2.0,
        )

    def test_age_not_five_is_none(self) -> None:
        for age in (4, 6, None):
            result = perfect_order.perfect_order_signal(
                _analysis(["join_trend"], age=age), "EUR_USD"
            )
            self.assertEqual(result["signal"], "none", age)
            self.assertIn("age=", result["reason"])

    def test_adx_not_rising_is_none(self) -> None:
        result = perfect_order.perfect_order_signal(
            _analysis(["join_trend"], rising=False), "EUR_USD"
        )
        self.assertEqual(result["signal"], "none")
        self.assertIn("ADX is not rising", result["reason"])

    def test_no_stack_is_none(self) -> None:
        result = perfect_order.perfect_order_signal(
            _analysis(["join_trend"], order=None), "EUR_USD"
        )
        self.assertEqual(result["signal"], "none")

    def test_play_class_gate(self) -> None:
        result = perfect_order.perfect_order_signal(
            _analysis(["fade_range"]), "EUR_USD"
        )
        self.assertEqual(result["signal"], "none")
        self.assertIn("regime allows", result["reason"])

    def test_trend_waning_is_none(self) -> None:
        result = perfect_order.perfect_order_signal(
            _analysis(["join_trend"], waning=True), "EUR_USD"
        )
        self.assertEqual(result["signal"], "none")
        self.assertIn("trend_waning", result["reason"])


class TestPerfectOrderEngine(unittest.TestCase):
    def test_confidence_positive_on_fire(self) -> None:
        analysis = _analysis(["join_trend"], adx=30.0)
        conf = perfect_order.signal_confidence("long", analysis)
        self.assertGreater(conf, 0.0)

    def test_confidence_zero_when_not_firing(self) -> None:
        analysis = _analysis(["join_trend"])
        self.assertEqual(perfect_order.signal_confidence("none", analysis), 0.0)


if __name__ == "__main__":
    unittest.main()
