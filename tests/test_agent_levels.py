"""Unit tests for Ch. 7 geometry tickets (no network)."""

from __future__ import annotations

import unittest

from agent.levels import apply_geometry, named_levels, pip_size, plan_ticket
from agent.schema import Proposal
from app import regime, risk as risk_lib
from tests.test_indicators import _range_bars, _trend_bars


class TestPipSize(unittest.TestCase):
    def test_usd_and_jpy(self) -> None:
        self.assertAlmostEqual(pip_size("GBP_USD"), 0.0001)
        self.assertAlmostEqual(pip_size("USD_JPY"), 0.01)
        self.assertAlmostEqual(pip_size("EUR/JPY"), 0.01)


class TestPlanTicket(unittest.TestCase):
    def test_join_trend_up_uses_last_close_and_low_n(self) -> None:
        analysis = regime.analyze_bars(_trend_bars(250, step=0.008))
        levels = named_levels(analysis)
        self.assertIn("last_close", levels)
        self.assertIn("low_n", levels)
        ticket = plan_ticket(analysis, "join_trend", "GBP_USD")
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["side"], "long")
        self.assertEqual(ticket["entry_name"], "last_close")
        self.assertEqual(ticket["stop_name"], "low_n")
        self.assertLess(ticket["stop"], ticket["entry"])
        self.assertGreaterEqual(
            risk_lib.r_multiple(ticket["entry"], ticket["stop"], ticket["target"]),
            2.0,
        )

    def test_fade_range_tickets(self) -> None:
        analysis = regime.analyze_bars(_range_bars(80, amp=0.0003))
        ticket = plan_ticket(analysis, "fade_range", "GBP_USD")
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertIn(ticket["side"], ("long", "short"))
        if ticket["side"] == "long":
            self.assertLess(ticket["stop"], ticket["entry"])
        else:
            self.assertGreater(ticket["stop"], ticket["entry"])
        self.assertGreaterEqual(
            risk_lib.r_multiple(ticket["entry"], ticket["stop"], ticket["target"]),
            2.0,
        )

    def test_breakout_watch_is_none(self) -> None:
        analysis = regime.analyze_bars(_trend_bars(250, step=0.008))
        self.assertIsNone(plan_ticket(analysis, "breakout_watch", "GBP_USD"))

    def test_waning_is_none(self) -> None:
        analysis = regime.analyze_bars(_trend_bars(250, step=0.008))
        analysis["trend_waning"] = True
        self.assertIsNone(plan_ticket(analysis, "join_trend", "GBP_USD"))

    def test_apply_overwrites_model_prices(self) -> None:
        analysis = regime.analyze_bars(_trend_bars(250, step=0.008))
        invented = Proposal(
            thesis="model invented a quote",
            play_class="join_trend",
            side="long",
            entry=9.99,
            stop=9.98,
            target=10.02,
            notes="from llm",
        )
        filled = apply_geometry(invented, analysis, "GBP_USD")
        self.assertIsNotNone(filled)
        assert filled is not None
        self.assertNotAlmostEqual(filled.entry or 0, 9.99)
        self.assertEqual(filled.side, "long")
        self.assertIn("ch7 geometry", filled.notes)
        self.assertEqual(filled.at_time, analysis["last_time"])

    def test_apply_strips_when_no_ticket(self) -> None:
        proposal = Proposal(
            thesis="watch",
            play_class="breakout_watch",
            side="long",
            entry=1.27,
            stop=1.26,
            target=1.29,
        )
        filled = apply_geometry(proposal, {"last_close": 1.27}, "GBP_USD")
        self.assertEqual(filled.side, "none")
        self.assertIsNone(filled.entry)
        self.assertIsNone(filled.stop)
        self.assertIsNone(filled.target)


if __name__ == "__main__":
    unittest.main()
