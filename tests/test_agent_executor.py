"""Unit tests for the stub paper executor (no broker)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.executor import process_pending, simulate_fill
from agent.journal import Journal
from agent.schema import Goal, Proposal, RiskVerdict, RunRecord


def _pending_record(run_id: str = "exec1", entry: float | None = 1.271) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        ts="2026-08-15T00:00:00+00:00",
        mode="paper",
        instrument="GBP_USD",
        granularity="D",
        action="pending_exec",
        goal=Goal(instrument="GBP_USD", mode="paper"),
        regime={"regime": "trend", "last_close": 1.2700, "trend_waning": False},
        proposal=Proposal(
            thesis="t",
            play_class="join_trend",
            side="long",
            entry=entry,
            stop=1.268,
            target=1.276,
        ),
        risk=RiskVerdict(ok=True, action="pending_exec", risk_fraction=0.02),
        citations=[],
        tool_trace=[],
    )


class TestExecutor(unittest.TestCase):
    def test_simulate_fill_uses_entry(self) -> None:
        fill = simulate_fill(_pending_record(entry=1.271))
        self.assertEqual(fill.status, "filled_sim")
        self.assertAlmostEqual(fill.fill_price, 1.271)

    def test_simulate_fill_falls_back_to_last_close(self) -> None:
        fill = simulate_fill(_pending_record(entry=None))
        self.assertEqual(fill.status, "filled_sim")
        self.assertAlmostEqual(fill.fill_price, 1.2700)

    def test_process_pending_marks_filled_sim(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        try:
            journal = Journal(Path(tmp.name) / "runs.sqlite")
            journal.append_run(_pending_record("exec2"))
            fills = process_pending(journal)
            self.assertEqual(len(fills), 1)
            self.assertEqual(fills[0].status, "filled_sim")
            self.assertEqual(journal.list_pending(), [])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
