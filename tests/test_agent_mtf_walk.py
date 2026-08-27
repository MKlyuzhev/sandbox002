"""Unit tests for causal Ch.8 MTF rollover-peak paper walk (no network)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent.engines import mtf as mtf_mod
from agent.journal import Journal
from agent.mtf_walk import htf_index_as_of, walk_mtf
from agent.schema import Goal


def _goal(**kwargs) -> Goal:
    data = {
        "instrument": "GBP_USD",
        "granularity": "D",
        "ltf_granularity": "H1",
        "mode": "paper",
        "no_rag": True,
        "no_llm": True,
        "balance": 10_000.0,
        "risk_fraction": 0.02,
    }
    data.update(kwargs)
    return Goal.model_validate(data)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")


def _hourly_bars(
    n: int,
    *,
    start: datetime | None = None,
    base_close: float = 1.20,
) -> list[dict]:
    origin = start or datetime(2024, 6, 1, tzinfo=timezone.utc)
    out: list[dict] = []
    for i in range(n):
        close = base_close + i * 0.0001
        out.append(
            {
                "time": _iso(origin + timedelta(hours=i)),
                "open": close,
                "high": close + 0.002,
                "low": close - 0.002,
                "close": close,
                "volume": 100,
                "complete": True,
            }
        )
    return out


def _daily_bars(
    n: int,
    *,
    start: datetime | None = None,
    base_close: float = 1.20,
) -> list[dict]:
    origin = start or datetime(2024, 5, 1, tzinfo=timezone.utc)
    out: list[dict] = []
    for i in range(n):
        close = base_close + i * 0.001
        out.append(
            {
                "time": _iso(origin + timedelta(days=i)),
                "open": close,
                "high": close + 0.01,
                "low": close - 0.01,
                "close": close,
                "volume": 1000,
                "complete": True,
            }
        )
    return out


def _htf_stub(window: list[dict]) -> dict:
    close = float(window[-1]["close"])
    return {
        "granularity": "D",
        "regime": "trend",
        "direction": "up",
        "trend_waning": False,
        "allowed_play_classes": ["join_trend"],
        "confidence": 0.8,
        "last_close": close,
        "snapshot": {"last_close": close, "sma": {}, "bollinger": {}},
    }


def _ltf_stub_factory(rsi_by_time: dict[str, float]):
    def _stub(window: list[dict]) -> dict:
        t = str(window[-1]["time"])
        close = float(window[-1]["close"])
        rsi = rsi_by_time.get(t, 55.0)
        return {
            "granularity": "H1",
            "last_close": close,
            "snapshot": {
                "last_close": close,
                "rsi": rsi,
                "high_n": close + 0.005,
                "low_n": close - 0.005,
                "sma": {},
                "bollinger": {},
            },
        }

    return _stub


def _conf_for_rsi(rsi: float) -> float:
    htf = _htf_stub([{"close": 1.2}])
    ltf = _ltf_stub_factory({})([{"time": "t", "close": 1.2}])
    ltf["snapshot"]["rsi"] = rsi
    out = mtf_mod.mtf_signal(htf, ltf, "GBP_USD")
    return mtf_mod.signal_confidence(
        out["signal"], htf, rsi, mtf_mod.RSI_OS, mtf_mod.RSI_OB
    )


class TestHtfIndexAsOf(unittest.TestCase):
    def test_last_htf_bar_on_or_before_ltf_time(self) -> None:
        htf = _daily_bars(5, start=datetime(2024, 6, 1, tzinfo=timezone.utc))
        idx = htf_index_as_of(htf, _iso(datetime(2024, 6, 3, 12, tzinfo=timezone.utc)))
        self.assertEqual(idx, 2)


class TestMtfWalkPeak(unittest.TestCase):
    def setUp(self) -> None:
        self.lookback = 40

    def _htf_for_ltf(self, ltf: list[dict]) -> list[dict]:
        """Enough daily history so causal HTF alignment satisfies ``lookback``."""
        first = datetime.fromisoformat(str(ltf[0]["time"]).replace("Z", "+00:00"))
        htf_start = first - timedelta(days=self.lookback + 30)
        span = (len(ltf) // 24) + self.lookback + 60
        return _daily_bars(span, start=htf_start)

    def _run(
        self,
        rsi_by_time: dict[str, float],
        *,
        ltf_n: int = 50,
        journal: Journal | None = None,
        walk_id: str | None = None,
        ltf_bars: list[dict] | None = None,
    ):
        ltf = ltf_bars or _hourly_bars(ltf_n)
        htf = self._htf_for_ltf(ltf)
        return walk_mtf(
            htf,
            ltf,
            _goal(),
            lookback=self.lookback,
            start_index=self.lookback - 1,
            htf_classify_fn=_htf_stub,
            ltf_classify_fn=_ltf_stub_factory(rsi_by_time),
            journal=journal,
            walk_id=walk_id,
        )

    def test_enter_at_peak_bar_not_rollover(self) -> None:
        ltf = _hourly_bars(50)
        t_peak = ltf[42]["time"]
        t_roll = ltf[43]["time"]
        rsi_map = {
            str(t_peak): 22.0,
            str(t_roll): 30.0,
        }
        result = self._run(rsi_map, ltf_bars=ltf)
        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertEqual(trade.entry_index, 42)
        self.assertEqual(trade.entry_time, str(t_peak))
        self.assertNotEqual(trade.entry_time, str(t_roll))

    def test_equal_confidence_does_not_roll(self) -> None:
        ltf = _hourly_bars(50)
        t_first = ltf[41]["time"]
        t_equal = ltf[42]["time"]
        t_roll = ltf[43]["time"]
        rsi_val = 25.0
        rsi_map = {
            str(t_first): rsi_val,
            str(t_equal): rsi_val,
            str(t_roll): 55.0,
        }
        result = self._run(rsi_map, ltf_bars=ltf)
        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].entry_index, 41)

    def test_non_fire_after_peak_confirms(self) -> None:
        ltf = _hourly_bars(50)
        t_peak = ltf[41]["time"]
        t_non = ltf[42]["time"]
        rsi_map = {str(t_peak): 24.0, str(t_non): 55.0}
        result = self._run(rsi_map, ltf_bars=ltf)
        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].entry_index, 41)

    def test_unconfirmed_peak_at_window_end(self) -> None:
        ltf = _hourly_bars(50)
        t_peak = ltf[49]["time"]
        rsi_map = {str(t_peak): 22.0}
        result = self._run(rsi_map, ltf_bars=ltf)
        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].entry_index, 49)
        self.assertEqual(result.trades[0].exit_status, "window_end")

    def test_ignore_peaks_while_in_trade_then_second_entry(self) -> None:
        ltf = _hourly_bars(55)
        t1 = ltf[41]["time"]
        t2 = ltf[42]["time"]
        t3 = ltf[43]["time"]
        t4 = ltf[48]["time"]
        t5 = ltf[49]["time"]
        rsi_map = {
            str(t1): 28.0,
            str(t2): 22.0,
            str(t3): 55.0,
            str(t4): 26.0,
            str(t5): 55.0,
        }
        ltf[44]["low"] = 0.5
        ltf[44]["high"] = ltf[44]["close"]
        result = self._run(rsi_map, ltf_bars=ltf)
        self.assertGreaterEqual(len(result.trades), 2)
        first, second = result.trades[0], result.trades[1]
        self.assertEqual(first.entry_index, 42)
        self.assertEqual(first.exit_status, "stop")
        self.assertGreater(second.entry_index, first.exit_index or 0)

    def test_target_exit(self) -> None:
        ltf = _hourly_bars(50)
        t_peak = ltf[41]["time"]
        t_roll = ltf[42]["time"]
        rsi_map = {str(t_peak): 24.0, str(t_roll): 55.0}
        ltf[43]["high"] = 9.0
        ltf[43]["low"] = ltf[43]["close"]
        result = self._run(rsi_map, ltf_bars=ltf)
        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].exit_status, "target")

    def test_journal_once_per_confirmed_peak(self) -> None:
        ltf = _hourly_bars(55)
        t1 = ltf[41]["time"]
        t2 = ltf[42]["time"]
        t3 = ltf[43]["time"]
        t4 = ltf[48]["time"]
        t5 = ltf[49]["time"]
        rsi_map = {
            str(t1): 28.0,
            str(t2): 22.0,
            str(t3): 55.0,
            str(t4): 26.0,
            str(t5): 55.0,
        }
        ltf[44]["low"] = 0.5
        ltf[44]["high"] = ltf[44]["close"]
        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal(Path(tmp) / "runs.sqlite")
            result = self._run(
                rsi_map, ltf_bars=ltf, journal=journal, walk_id="mtf-walk-j"
            )
            runs = journal.list_runs(walk_id="mtf-walk-j", limit=500)
            fills = journal.list_fills_for_walk("mtf-walk-j")
            self.assertEqual(len(result.trades), len(runs))
            self.assertEqual(len(result.trades), len(fills))
            self.assertGreaterEqual(len(runs), 2)
            for run in runs:
                self.assertEqual(run.action, "pending_exec")
                self.assertIsNotNone(run.proposal)
                assert run.proposal is not None
                self.assertEqual(run.proposal.engine, "mtf")
                self.assertGreater(run.proposal.confidence, 0)


class TestMtfWalkConfidence(unittest.TestCase):
    def test_confidence_ordering_matches_engine(self) -> None:
        c22 = _conf_for_rsi(22.0)
        c25 = _conf_for_rsi(25.0)
        c28 = _conf_for_rsi(28.0)
        self.assertGreater(c22, c25)
        self.assertGreater(c25, c28)


if __name__ == "__main__":
    unittest.main()
