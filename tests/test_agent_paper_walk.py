"""Unit tests for causal paper walk (no network)."""

from __future__ import annotations

import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent.journal import Journal
from agent.paper_walk import check_exit, summarize_equity, walk_paper
from agent.schema import Goal, PaperTrade
from app import mt4_bridge
from app.risk import apply_r_to_equity
from tests.test_indicators import _range_bars, _trend_bars


def _stamp(bars: list[dict], start: datetime | None = None) -> list[dict]:
    origin = start or datetime(2024, 1, 1, tzinfo=timezone.utc)
    out: list[dict] = []
    for i, b in enumerate(bars):
        nb = dict(b)
        ts = origin + timedelta(days=i)
        nb["time"] = ts.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
        nb["complete"] = True
        out.append(nb)
    return out


def _goal(**kwargs) -> Goal:
    data = {
        "instrument": "GBP_USD",
        "granularity": "D",
        "mode": "paper",
        "no_rag": True,
        "no_llm": True,
        "balance": 10_000.0,
        "risk_fraction": 0.02,
    }
    data.update(kwargs)
    return Goal.model_validate(data)


def _pass_up(bar: dict) -> dict:
    close = float(bar["close"])
    return {
        "regime": "trend",
        "direction": "up",
        "trend_waning": False,
        "allowed_play_classes": ["join_trend"],
        "last_close": close,
        "snapshot": {
            "last_close": close,
            "high_n": close + 0.005,
            "low_n": close - 0.005,
            "sma": {},
            "bollinger": {},
        },
    }


def _pass_up_window(window: list[dict]) -> dict:
    return _pass_up(window[-1])


def _walk_trades(*args, **kwargs):
    return walk_paper(*args, **kwargs).trades


class TestCheckExit(unittest.TestCase):
    def test_stop_wins_when_both_trade(self) -> None:
        bar = {"high": 1.30, "low": 1.20, "close": 1.25, "open": 1.25}
        hit = check_exit("long", stop=1.22, target=1.28, bar=bar)
        self.assertEqual(hit, ("stop", 1.22))

    def test_short_target(self) -> None:
        bar = {"high": 1.21, "low": 1.18, "close": 1.19, "open": 1.20}
        hit = check_exit("short", stop=1.22, target=1.18, bar=bar)
        self.assertEqual(hit, ("target", 1.18))


class TestWalkPaper(unittest.TestCase):
    def test_mutating_future_bar_does_not_change_entry(self) -> None:
        bars = _stamp(_trend_bars(60, start=1.2, step=0.01))
        lookback = 40
        first = _walk_trades(
            bars, _goal(), lookback=lookback, start_index=lookback - 1,
            classify_fn=_pass_up_window,
        )
        self.assertTrue(first)
        entry_i = first[0].entry_index
        self.assertLess(entry_i + 1, len(bars))
        mutated = copy.deepcopy(bars)
        mutated[entry_i + 1]["high"] = 99.0
        mutated[entry_i + 1]["low"] = 0.01
        mutated[entry_i + 1]["close"] = 50.0
        mutated[entry_i + 1]["open"] = 50.0
        second = _walk_trades(
            mutated, _goal(), lookback=lookback, start_index=lookback - 1,
            classify_fn=_pass_up_window,
        )
        self.assertEqual(first[0].entry_index, second[0].entry_index)
        self.assertEqual(first[0].entry_time, second[0].entry_time)
        self.assertEqual(first[0].entry, second[0].entry)
        self.assertEqual(first[0].side, second[0].side)

    def test_stop_then_can_enter_again(self) -> None:
        bars = _stamp(_trend_bars(55, start=1.2, step=0.002))
        lookback = 40
        smash = bars[lookback]
        smash["low"] = 0.5
        smash["high"] = smash["close"]
        tmp = tempfile.TemporaryDirectory()
        try:
            journal = Journal(Path(tmp.name) / "runs.sqlite")
            result = walk_paper(
                bars,
                _goal(),
                lookback=lookback,
                start_index=lookback - 1,
                journal=journal,
                classify_fn=_pass_up_window,
                walk_id="walk-stop",
            )
            trades = result.trades
            self.assertGreaterEqual(len(trades), 2)
            first, second = trades[0], trades[1]
            self.assertEqual(first.entry_index, lookback - 1)
            self.assertEqual(first.exit_status, "stop")
            self.assertEqual(first.exit_index, lookback)
            self.assertGreater(second.entry_index, first.exit_index)
            self.assertEqual(result.walk_id, "walk-stop")
            self.assertEqual(first.walk_id, "walk-stop")
            self.assertEqual(second.walk_id, "walk-stop")
            self.assertAlmostEqual(first.r_realized, -1.0)
            self.assertAlmostEqual(first.pnl, -200.0)
            self.assertAlmostEqual(first.equity_after, 9800.0)
            self.assertAlmostEqual(
                second.pnl, 9800.0 * 0.02 * second.r_realized, places=3
            )
            self.assertAlmostEqual(
                second.equity_after, 9800.0 + second.pnl, places=3
            )
            self.assertAlmostEqual(
                result.equity.ending_equity, trades[-1].equity_after
            )
            listed = journal.list_runs(limit=50, walk_id="walk-stop")
            self.assertEqual(len(listed), len(trades))
            fill = journal.get_fill(first.run_id)
            self.assertAlmostEqual(fill.pnl, -200.0)
            self.assertAlmostEqual(fill.equity_after, 9800.0)
            self.assertEqual(fill.walk_id, "walk-stop")
            listed = journal.list_runs(limit=50)
            self.assertEqual(len(listed), len(trades))
            by_id = {r.run_id: r for r in listed}
            for trade in trades:
                row = by_id[trade.run_id]
                self.assertEqual(row.proposal.side, trade.side)
                self.assertEqual(row.proposal.stop, trade.stop)
                self.assertEqual(row.proposal.target, trade.target)
                self.assertEqual(row.proposal.at_time, trade.entry_time)
                fill = journal.get_fill(trade.run_id)
                self.assertIsNotNone(fill)
                self.assertEqual(fill.exit_status, trade.exit_status)
        finally:
            tmp.cleanup()

    def test_same_bar_stop_and_target_is_stop(self) -> None:
        bars = _stamp(_trend_bars(42, start=1.2, step=0.002))
        lookback = 40
        both = bars[lookback]
        both["low"] = 0.5
        both["high"] = 10.0
        trades = _walk_trades(
            bars,
            _goal(),
            lookback=lookback,
            start_index=lookback - 1,
            classify_fn=_pass_up_window,
        )
        self.assertTrue(trades)
        self.assertEqual(trades[0].exit_status, "stop")
        self.assertEqual(trades[0].exit_price, trades[0].stop)

    def test_window_end_if_never_stopped(self) -> None:
        bars = _stamp(_trend_bars(45, start=1.2, step=0.002))
        lookback = 40

        def classify(window: list[dict]) -> dict:
            analysis = _pass_up(window[-1])
            close = analysis["last_close"]
            # Stop/target far from later bar ranges so nothing hits.
            analysis["snapshot"]["low_n"] = close - 0.5
            analysis["snapshot"]["high_n"] = close + 0.5
            return analysis

        trades = _walk_trades(
            bars, _goal(), lookback=lookback, start_index=lookback - 1,
            classify_fn=classify,
        )
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].exit_status, "window_end")
        self.assertEqual(trades[0].exit_index, len(bars) - 1)
        self.assertAlmostEqual(trades[0].exit_price, float(bars[-1]["close"]))

    def test_waning_never_enters(self) -> None:
        bars = _stamp(_trend_bars(50, start=1.2, step=0.01))

        def classify(window: list[dict]) -> dict:
            close = float(window[-1]["close"])
            return {
                "regime": "mixed",
                "direction": "up",
                "trend_waning": True,
                "allowed_play_classes": ["breakout_watch"],
                "last_close": close,
                "snapshot": {"last_close": close},
            }

        result = walk_paper(
            bars, _goal(), lookback=40, start_index=39, classify_fn=classify
        )
        self.assertEqual(result.trades, [])
        self.assertEqual(result.equity.trade_count, 0)
        self.assertAlmostEqual(result.equity.ending_equity, 10_000.0)

    def test_breakout_watch_never_enters(self) -> None:
        bars = _stamp(_range_bars(50, amp=0.0003))

        def classify(window: list[dict]) -> dict:
            close = float(window[-1]["close"])
            return {
                "regime": "mixed",
                "direction": None,
                "trend_waning": False,
                "allowed_play_classes": ["breakout_watch"],
                "last_close": close,
                "snapshot": {"last_close": close},
            }

        trades = _walk_trades(
            bars, _goal(), lookback=40, start_index=39, classify_fn=classify
        )
        self.assertEqual(trades, [])

    def test_journals_filled_not_pending(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        try:
            journal = Journal(Path(tmp.name) / "runs.sqlite")
            bars = _stamp(_trend_bars(45, start=1.2, step=0.002))
            trades = _walk_trades(
                bars,
                _goal(),
                lookback=40,
                start_index=39,
                journal=journal,
                classify_fn=_pass_up_window,
            )
            self.assertTrue(trades)
            self.assertEqual(journal.list_pending(), [])
            fill = journal.get_fill(trades[0].run_id)
            self.assertIsNotNone(fill)
            self.assertEqual(fill.status, "filled_sim")
            self.assertEqual(fill.exit_status, trades[0].exit_status)
            self.assertEqual(fill.fill_price, trades[0].entry)
            loaded = journal.get_run(trades[0].run_id)
            self.assertEqual(loaded.ts, trades[0].entry_time)
            self.assertEqual(loaded.proposal.at_time, trades[0].entry_time)
            listed = journal.list_runs(limit=50)
            self.assertEqual(len(listed), len(trades))
            by_id = {r.run_id: r for r in listed}
            for trade in trades:
                row = by_id[trade.run_id]
                self.assertEqual(row.proposal.side, trade.side)
                self.assertEqual(row.proposal.stop, trade.stop)
                self.assertEqual(row.proposal.target, trade.target)
                self.assertEqual(row.proposal.at_time, trade.entry_time)
        finally:
            tmp.cleanup()

    def test_mt4_objects_mark_direction_stop_and_target(self) -> None:
        bars = _stamp(_trend_bars(45, start=1.2, step=0.002))
        trades = _walk_trades(
            bars, _goal(), lookback=40, start_index=39, classify_fn=_pass_up_window
        )
        self.assertTrue(trades)
        objs = mt4_bridge.paper_walk_to_objects(
            [t.model_dump(mode="json") for t in trades]
        )
        types = {o["type"] for o in objs}
        names = {o["name"]: o for o in objs}
        self.assertIn("arrow", types)
        self.assertIn("text", types)
        self.assertIn("trend", types)
        self.assertNotIn("hline", types)
        self.assertTrue(all(o["name"].startswith("sbox.ticket.walk.") for o in objs))
        first = names["sbox.ticket.walk.0.arrow"]
        self.assertEqual(first["arrow_code"], 233 if trades[0].side == "long" else 234)
        self.assertEqual(names["sbox.ticket.walk.0.side"]["text"], trades[0].side)
        stop_obj = names["sbox.ticket.walk.0.stop"]
        self.assertEqual(stop_obj["p1"], trades[0].stop)
        self.assertEqual(stop_obj["p2"], trades[0].stop)
        self.assertEqual(stop_obj["color"], "red")
        target_obj = names["sbox.ticket.walk.0.target"]
        self.assertEqual(target_obj["p1"], trades[0].target)
        self.assertEqual(target_obj["p2"], trades[0].target)
        self.assertEqual(target_obj["color"], "green")
        self.assertNotEqual(mt4_bridge.TICKET_WALK_PREFIX, mt4_bridge.TICKET_PREFIX)
        self.assertNotEqual(mt4_bridge.TICKET_WALK_PREFIX, mt4_bridge.REGIME_WALK_PREFIX)


class TestEquityStats(unittest.TestCase):
    def test_compound_two_full_stops(self) -> None:
        pnl, equity = apply_r_to_equity(10_000.0, 0.02, -1.0)
        self.assertAlmostEqual(pnl, -200.0)
        self.assertAlmostEqual(equity, 9800.0)
        pnl2, equity2 = apply_r_to_equity(equity, 0.02, -1.0)
        self.assertAlmostEqual(pnl2, -196.0)
        self.assertAlmostEqual(equity2, 9604.0)

    def test_summarize_wins_losses_drawdown(self) -> None:
        t1 = PaperTrade(
            run_id="a",
            entry_index=0,
            entry_time="t1",
            side="long",
            play_class="join_trend",
            entry=1.2,
            stop=1.1,
            target=1.4,
            r_realized=-1.0,
            pnl=-200.0,
            equity_after=9800.0,
        )
        t2 = PaperTrade(
            run_id="b",
            entry_index=1,
            entry_time="t2",
            side="long",
            play_class="join_trend",
            entry=1.2,
            stop=1.1,
            target=1.4,
            r_realized=-1.0,
            pnl=-196.0,
            equity_after=9604.0,
        )
        t3 = PaperTrade(
            run_id="c",
            entry_index=2,
            entry_time="t3",
            side="long",
            play_class="join_trend",
            entry=1.2,
            stop=1.1,
            target=1.4,
            r_realized=0.0,
            pnl=0.0,
            equity_after=9604.0,
        )
        t4 = PaperTrade(
            run_id="d",
            entry_index=3,
            entry_time="t4",
            side="long",
            play_class="join_trend",
            entry=1.2,
            stop=1.1,
            target=1.4,
            r_realized=2.0,
            pnl=384.16,
            equity_after=9988.16,
        )
        eq = summarize_equity("walk-eq", [t1, t2, t3, t4], 10_000.0, 0.02)
        self.assertEqual(eq.walk_id, "walk-eq")
        self.assertEqual(eq.trade_count, 4)
        self.assertEqual(eq.wins, 1)
        self.assertEqual(eq.losses, 2)
        self.assertEqual(eq.scratches, 1)
        self.assertAlmostEqual(eq.win_rate, 0.25)
        self.assertAlmostEqual(eq.sum_r, 0.0)
        self.assertAlmostEqual(eq.mean_r, 0.0)
        self.assertAlmostEqual(eq.ending_equity, 9988.16)
        self.assertAlmostEqual(eq.max_drawdown, 396.0)
        self.assertAlmostEqual(eq.max_drawdown_frac, 396.0 / 10_000.0)


if __name__ == "__main__":
    unittest.main()
