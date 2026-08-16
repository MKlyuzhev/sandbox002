"""Unit tests for agent.journal (tempfile SQLite, no network)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.journal import Journal
from agent.schema import Goal, Proposal, RiskVerdict, RunRecord, SimFill


def _record(
    action: str = "log_setup",
    run_id: str = "run1",
    ts: str = "2026-08-15T00:00:00+00:00",
    instrument: str = "GBP_USD",
) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        ts=ts,
        mode="paper" if action == "pending_exec" else "signal",
        instrument=instrument,
        granularity="D",
        action=action,  # type: ignore[arg-type]
        goal=Goal(instrument="GBP_USD", mode="paper" if action == "pending_exec" else "signal"),
        regime={"regime": "trend", "last_close": 1.27, "trend_waning": False},
        proposal=Proposal(
            thesis="t",
            play_class="join_trend",
            side="long",
            entry=1.27,
            stop=1.268,
            target=1.274,
        ),
        risk=RiskVerdict(ok=True, action=action, risk_fraction=0.02),  # type: ignore[arg-type]
        citations=[],
        tool_trace=[],
    )


class TestJournal(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "runs.sqlite"
        self.journal = Journal(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_round_trip(self) -> None:
        original = _record()
        self.journal.append_run(original)
        loaded = self.journal.get_run("run1")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.run_id, original.run_id)
        self.assertEqual(loaded.action, "log_setup")
        self.assertEqual(loaded.proposal.entry, 1.27)
        self.assertEqual(loaded.regime["last_close"], 1.27)
        self.assertEqual(self.journal.list_pending(), [])

    def test_pending_exec_enqueues_fill(self) -> None:
        self.journal.append_run(_record(action="pending_exec", run_id="run2"))
        pending = self.journal.list_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].run_id, "run2")

    def test_record_fill_clears_pending(self) -> None:
        self.journal.append_run(_record(action="pending_exec", run_id="run3"))
        fill = SimFill(
            run_id="run3",
            status="filled_sim",
            fill_price=1.27,
            ts="2026-08-15T01:00:00+00:00",
            note="stub",
        )
        self.journal.record_fill(fill)
        self.assertEqual(self.journal.list_pending(), [])

    def test_list_runs_newest_first_and_filter(self) -> None:
        self.journal.append_run(
            _record(run_id="old", ts="2026-08-01T00:00:00+00:00", instrument="EUR_USD")
        )
        self.journal.append_run(
            _record(run_id="new", ts="2026-08-15T00:00:00+00:00", action="wait")
        )
        listed = self.journal.list_runs(limit=10)
        self.assertEqual([r.run_id for r in listed], ["new", "old"])
        only_eur = self.journal.list_runs(instrument="EUR_USD")
        self.assertEqual([r.run_id for r in only_eur], ["old"])
        only_wait = self.journal.list_runs(action="wait")
        self.assertEqual([r.run_id for r in only_wait], ["new"])


if __name__ == "__main__":
    unittest.main()
