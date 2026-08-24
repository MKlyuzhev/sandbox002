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

    def test_walk_fill_skips_executor_queue_and_records_exit(self) -> None:
        self.journal.append_run(
            _record(action="pending_exec", run_id="walk1"), queue_fill=False
        )
        self.assertEqual(self.journal.list_pending(), [])
        fill = SimFill(
            run_id="walk1",
            status="filled_sim",
            fill_price=1.27,
            ts="2024-01-01T00:00:00+00:00",
            note="walk fill",
        )
        self.journal.record_fill(fill)
        self.journal.record_exit(
            SimFill(
                run_id="walk1",
                status="filled_sim",
                fill_price=1.27,
                ts="2024-01-01T00:00:00+00:00",
                note="walk exit stop",
                exit_status="stop",
                exit_price=1.268,
                exit_ts="2024-01-02T00:00:00+00:00",
                r_realized=-1.0,
            )
        )
        loaded = self.journal.get_fill("walk1")
        self.assertEqual(loaded.status, "filled_sim")
        self.assertEqual(loaded.exit_status, "stop")
        self.assertEqual(loaded.exit_price, 1.268)
        self.assertEqual(loaded.r_realized, -1.0)
        self.assertEqual(self.journal.list_pending(), [])

    def test_walk_id_and_equity_persist(self) -> None:
        record = _record(action="pending_exec", run_id="walk1")
        record = record.model_copy(update={"walk_id": "abc123"})
        self.journal.append_run(record, queue_fill=False)
        self.journal.record_fill(
            SimFill(
                run_id="walk1",
                status="filled_sim",
                fill_price=1.27,
                ts="2024-01-01T00:00:00+00:00",
                note="walk fill",
                walk_id="abc123",
            )
        )
        self.journal.record_exit(
            SimFill(
                run_id="walk1",
                status="filled_sim",
                fill_price=1.27,
                ts="2024-01-01T00:00:00+00:00",
                note="walk exit stop",
                exit_status="stop",
                exit_price=1.268,
                exit_ts="2024-01-02T00:00:00+00:00",
                r_realized=-1.0,
                walk_id="abc123",
                pnl=-200.0,
                equity_after=9800.0,
            )
        )
        loaded_run = self.journal.get_run("walk1")
        self.assertEqual(loaded_run.walk_id, "abc123")
        loaded = self.journal.get_fill("walk1")
        self.assertEqual(loaded.walk_id, "abc123")
        self.assertEqual(loaded.pnl, -200.0)
        self.assertEqual(loaded.equity_after, 9800.0)
        listed = self.journal.list_runs(walk_id="abc123")
        self.assertEqual([r.run_id for r in listed], ["walk1"])
        self.assertEqual(self.journal.list_runs(walk_id="missing"), [])
        fills = self.journal.list_fills_for_walk("abc123")
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0].equity_after, 9800.0)

    def test_list_runs_newest_write_not_oldest_decision_bar(self) -> None:
        self.journal.append_run(
            _record(run_id="live", ts="2026-08-16T00:00:00+00:00")
        )
        self.journal.append_run(
            _record(run_id="walk-2024", ts="2024-01-15T00:00:00.000000000Z")
        )
        listed = self.journal.list_runs(limit=10)
        self.assertEqual([r.run_id for r in listed], ["walk-2024", "live"])

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
