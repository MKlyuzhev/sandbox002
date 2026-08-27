"""Ch7Engine reproduces levels.plan_ticket geometry via the engine interface."""

from __future__ import annotations

import unittest

from agent import levels as levels_mod
from agent.engines.base import EngineContext
from agent.engines.ch7 import Ch7Engine
from agent.schema import Goal
from app import regime
from tests.test_indicators import _range_bars, _trend_bars


def _ctx(analysis: dict) -> EngineContext:
    return EngineContext(
        instrument="GBP_USD",
        goal=Goal(instrument="GBP_USD", granularity="D"),
        analyses={"D": analysis},
    )


class TestCh7Engine(unittest.TestCase):
    def test_trend_fires_join_trend_matching_plan_ticket(self) -> None:
        analysis = regime.analyze_bars(_trend_bars(250, step=0.008))
        result = Ch7Engine().signal(_ctx(analysis))
        self.assertTrue(result.firing)
        self.assertEqual(result.play_class, "join_trend")
        expected = levels_mod.plan_ticket(analysis, "join_trend", "GBP_USD")
        self.assertEqual(result.ticket, expected)

    def test_range_fires_fade_range(self) -> None:
        analysis = regime.analyze_bars(_range_bars(80, amp=0.0003))
        result = Ch7Engine().signal(_ctx(analysis))
        self.assertTrue(result.firing)
        self.assertEqual(result.play_class, "fade_range")

    def test_confidence_is_discounted_regime_confidence(self) -> None:
        analysis = regime.analyze_bars(_trend_bars(250, step=0.008))
        result = Ch7Engine().signal(_ctx(analysis))
        self.assertAlmostEqual(
            result.confidence,
            round(Ch7Engine.FALLBACK_WEIGHT * float(analysis["confidence"]), 3),
        )

    def test_waning_does_not_fire(self) -> None:
        analysis = regime.analyze_bars(_trend_bars(250, step=0.008))
        analysis["trend_waning"] = True
        result = Ch7Engine().signal(_ctx(analysis))
        self.assertFalse(result.firing)


if __name__ == "__main__":
    unittest.main()
