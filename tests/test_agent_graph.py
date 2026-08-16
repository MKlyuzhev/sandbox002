"""Unit tests for agent.graph (injected bars + proposer, no network)."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from agent.graph import run
from agent.journal import Journal
from agent.schema import Goal, Proposal
from tests.test_indicators import _range_bars, _trend_bars


def _goal(**kwargs) -> Goal:
    data = {
        "instrument": "GBP_USD",
        "granularity": "D",
        "no_rag": True,
        "no_llm": True,
    }
    data.update(kwargs)
    return Goal.model_validate(data)


def _join_trend_proposal(regime: dict, _chunks, _goal: Goal) -> Proposal:
    close = float(regime["last_close"])
    return Proposal(
        thesis="join trend on daily",
        play_class="join_trend",
        side="long" if regime.get("direction") == "up" else "short",
        entry=close,
        stop=close - 0.002 if regime.get("direction") == "up" else close + 0.002,
        target=close + 0.004 if regime.get("direction") == "up" else close - 0.004,
        confidence=0.6,
    )


def _fade_proposal(regime: dict, _chunks, _goal: Goal) -> Proposal:
    close = float(regime["last_close"])
    return Proposal(
        thesis="wrong play for a trend",
        play_class="fade_range",
        side="short",
        entry=close,
        stop=close + 0.002,
        target=close - 0.004,
        confidence=0.5,
    )


class TestGraph(unittest.TestCase):
    def test_trend_setup_logs(self) -> None:
        record = asyncio.run(
            run(
                _goal(),
                bars=_trend_bars(250, step=0.008),
                propose_fn=_join_trend_proposal,
            )
        )
        self.assertIsNone(record.error)
        self.assertEqual(record.regime["regime"], "trend")
        self.assertEqual(record.action, "log_setup")
        self.assertTrue(record.risk.ok)
        self.assertGreaterEqual(record.risk.r_planned, 2.0)

    def test_mismatch_waits(self) -> None:
        record = asyncio.run(
            run(
                _goal(),
                bars=_trend_bars(250, step=0.008),
                propose_fn=_fade_proposal,
            )
        )
        self.assertEqual(record.action, "wait")
        self.assertFalse(record.risk.ok)

    def test_waning_skips_proposer(self) -> None:
        called = {"n": 0}

        def classify(_bars):
            return {
                "regime": "mixed",
                "direction": "up",
                "trend_waning": True,
                "allowed_play_classes": ["breakout_watch"],
                "last_close": 1.2,
                "notes": ["trend_waning"],
            }

        def boom(*_args):
            called["n"] += 1
            raise AssertionError("proposer must not run when trend_waning")

        record = asyncio.run(
            run(
                _goal(),
                bars=_trend_bars(80, step=0.01),
                classify_fn=classify,
                propose_fn=boom,
            )
        )
        self.assertEqual(called["n"], 0)
        self.assertEqual(record.action, "wait")
        self.assertIsNone(record.proposal)

    def test_paper_mode_journals_pending(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        try:
            journal = Journal(Path(tmp.name) / "runs.sqlite")
            record = asyncio.run(
                run(
                    _goal(mode="paper", no_rag=True),
                    bars=_trend_bars(250, step=0.008),
                    propose_fn=_join_trend_proposal,
                    journal=journal,
                )
            )
            self.assertEqual(record.action, "pending_exec")
            pending = journal.list_pending()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].run_id, record.run_id)
        finally:
            tmp.cleanup()

    def test_no_llm_geometry_logs_setup(self) -> None:
        record = asyncio.run(
            run(_goal(no_llm=True), bars=_trend_bars(250, step=0.008))
        )
        self.assertIsNone(record.error)
        self.assertEqual(record.action, "log_setup")
        self.assertEqual(record.proposal.play_class, "join_trend")
        self.assertEqual(record.proposal.side, "long")
        self.assertIsNotNone(record.proposal.entry)
        self.assertTrue(record.risk.ok)

    def test_no_llm_range_fades(self) -> None:
        record = asyncio.run(
            run(_goal(no_llm=True), bars=_range_bars(80, amp=0.0003))
        )
        self.assertEqual(record.proposal.play_class, "fade_range")
        self.assertIn(record.proposal.side, ("long", "short"))
        self.assertEqual(record.action, "log_setup")

    def test_no_llm_breakout_watch_waits(self) -> None:
        def classify(_bars):
            return {
                "regime": "mixed",
                "direction": None,
                "trend_waning": False,
                "allowed_play_classes": ["breakout_watch"],
                "last_close": 1.2,
                "snapshot": {"last_close": 1.2},
            }

        record = asyncio.run(
            run(
                _goal(no_llm=True),
                bars=_range_bars(80, amp=0.0003),
                classify_fn=classify,
            )
        )
        self.assertEqual(record.action, "wait")
        self.assertEqual(record.proposal.side, "none")
        self.assertIsNone(record.proposal.entry)

    def test_paper_no_llm_queues_pending(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        try:
            journal = Journal(Path(tmp.name) / "runs.sqlite")
            record = asyncio.run(
                run(
                    _goal(mode="paper", no_llm=True, no_rag=True),
                    bars=_trend_bars(250, step=0.008),
                    journal=journal,
                )
            )
            self.assertEqual(record.action, "pending_exec")
            self.assertEqual(len(journal.list_pending()), 1)
        finally:
            tmp.cleanup()

    def test_mt4_draws_ticket_after_pass(self) -> None:
        drawn = {}

        def regime_draw(*_args, **_kwargs):
            return {"ok": True}

        def ticket_draw(instrument, granularity, entry, stop, target, **kwargs):
            drawn["instrument"] = instrument
            drawn["entry"] = entry
            drawn["stop"] = stop
            drawn["target"] = target
            drawn["side"] = kwargs.get("side")
            drawn["prefix"] = kwargs.get("prefix")
            drawn["at_time"] = kwargs.get("at_time")
            return {"ok": True, "cmd_id": "x"}

        record = asyncio.run(
            run(
                _goal(mt4=True, no_llm=True),
                bars=_trend_bars(250, step=0.008),
                apply_mt4_fn=regime_draw,
                apply_mt4_ticket_fn=ticket_draw,
            )
        )
        self.assertEqual(record.action, "log_setup")
        self.assertTrue(drawn)
        self.assertEqual(drawn["instrument"], "GBP_USD")
        self.assertEqual(drawn["side"], "long")
        self.assertEqual(drawn["prefix"], "sbox.ticket.")
        self.assertEqual(drawn["at_time"], record.regime.get("last_time"))
        self.assertAlmostEqual(drawn["entry"], record.proposal.entry)
        self.assertTrue(record.regime.get("mt4_ticket", {}).get("ok"))


if __name__ == "__main__":
    unittest.main()
