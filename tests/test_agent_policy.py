"""Unit tests for agent.policy (no network)."""

from __future__ import annotations

import unittest

from agent import policy
from agent.schema import Goal, Proposal
from app import risk as risk_lib


def _goal(**kwargs) -> Goal:
    data = {"instrument": "GBP_USD", "granularity": "D"}
    data.update(kwargs)
    return Goal.model_validate(data)


def _proposal(**kwargs) -> Proposal:
    data = {
        "thesis": "join the daily uptrend",
        "play_class": "join_trend",
        "side": "long",
        "entry": 1.2700,
        "stop": 1.2680,
        "target": 1.2740,
        "confidence": 0.7,
    }
    data.update(kwargs)
    return Proposal.model_validate(data)


def _regime(**kwargs) -> dict:
    data = {
        "regime": "trend",
        "direction": "up",
        "trend_waning": False,
        "allowed_play_classes": ["join_trend"],
        "last_close": 1.2700,
    }
    data.update(kwargs)
    return data


class TestPolicyGates(unittest.TestCase):
    def test_waning_waits(self) -> None:
        verdict = policy.evaluate(
            _regime(trend_waning=True, regime="mixed", allowed_play_classes=["breakout_watch"]),
            _proposal(),
            _goal(),
        )
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.action, "wait")
        self.assertTrue(any("trend_waning" in r for r in verdict.reasons))

    def test_play_class_mismatch_waits(self) -> None:
        verdict = policy.evaluate(
            _regime(),
            _proposal(play_class="fade_range"),
            _goal(),
        )
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.action, "wait")
        self.assertTrue(any("not in allowed_play_classes" in r for r in verdict.reasons))

    def test_r_below_two_waits(self) -> None:
        verdict = policy.evaluate(
            _regime(),
            _proposal(target=1.2710),
            _goal(),
        )
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.action, "wait")
        self.assertIsNotNone(verdict.r_planned)
        self.assertLess(verdict.r_planned, policy.MIN_R)

    def test_two_r_passes_and_sizes(self) -> None:
        proposal = _proposal()
        goal = _goal(balance=10_000.0, risk_fraction=0.02)
        verdict = policy.evaluate(_regime(), proposal, goal)
        self.assertTrue(verdict.ok)
        self.assertEqual(verdict.action, "log_setup")
        self.assertAlmostEqual(verdict.r_planned, 2.0)
        expected = risk_lib.position_size(10_000.0, 0.02, 0.002)
        self.assertAlmostEqual(verdict.size_units, expected)

    def test_paper_mode_queues_pending_exec(self) -> None:
        verdict = policy.evaluate(_regime(), _proposal(), _goal(mode="paper"))
        self.assertTrue(verdict.ok)
        self.assertEqual(verdict.action, "pending_exec")

    def test_breakout_watch_never_pending_exec(self) -> None:
        verdict = policy.evaluate(
            _regime(regime="mixed", allowed_play_classes=["breakout_watch"]),
            _proposal(play_class="breakout_watch"),
            _goal(mode="paper"),
        )
        self.assertTrue(verdict.ok)
        self.assertEqual(verdict.action, "log_setup")

    def test_exposure_cap_rejects(self) -> None:
        verdict = policy.evaluate(
            _regime(),
            _proposal(),
            _goal(open_risk_fraction=0.05, risk_fraction=0.02, exposure_cap=0.06),
        )
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.action, "wait")


if __name__ == "__main__":
    unittest.main()
