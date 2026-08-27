"""Unit tests for the MT4 Strategy Tester feed (no network, no MT4)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from agent.mtf_walk import mtf_decisions
from agent.schema import Goal
from app import mt4_tester
from tests.test_agent_mtf_walk import (
    _daily_bars,
    _htf_stub,
    _hourly_bars,
    _ltf_stub_factory,
)


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


class TestTimeConversion(unittest.TestCase):
    def test_unix_rfc3339_round_trip(self) -> None:
        sec = 1_717_243_200  # 2024-06-01T12:00:00Z
        iso = mt4_tester.unix_to_rfc3339(sec)
        self.assertTrue(iso.startswith("2024-06-01T12:00:00"))
        self.assertEqual(mt4_tester.rfc3339_to_unix(iso), sec)


class TestBarsCsv(unittest.TestCase):
    def test_round_trip_and_sort(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bars.csv"
            path.write_text(
                "time,open,high,low,close,volume\n"
                "1717250400,1.2700,1.2720,1.2690,1.2710,120\n"
                "1717246800,1.2680,1.2705,1.2675,1.2700,100\n"  # earlier, out of order
            )
            bars = mt4_tester.read_bars_csv(path)
            self.assertEqual(len(bars), 2)
            self.assertLess(bars[0]["time_unix"], bars[1]["time_unix"])
            self.assertEqual(bars[0]["open"], 1.2680)
            self.assertEqual(bars[1]["close"], 1.2710)
            self.assertTrue(bars[0]["time"].startswith("2024-"))
            self.assertTrue(all(b["complete"] for b in bars))

    def test_missing_file_raises(self) -> None:
        with self.assertRaises(mt4_tester.TesterFeedError):
            mt4_tester.read_bars_csv(Path("/no/such/bars.csv"))


class TestResample(unittest.TestCase):
    def test_hourly_to_daily_aggregation(self) -> None:
        # Two UTC days of hourly bars.
        start = datetime(2024, 6, 1, 0, tzinfo=timezone.utc)
        hourly = _hourly_bars(48, start=start)
        for b in hourly:
            b["time_unix"] = mt4_tester.rfc3339_to_unix(b["time"])
        daily = mt4_tester.resample_bars(hourly, "D")
        self.assertEqual(len(daily), 2)
        day0 = daily[0]
        members0 = hourly[:24]
        self.assertEqual(day0["open"], members0[0]["open"])
        self.assertEqual(day0["close"], members0[-1]["close"])
        self.assertEqual(day0["high"], max(m["high"] for m in members0))
        self.assertEqual(day0["low"], min(m["low"] for m in members0))
        self.assertEqual(day0["volume"], sum(m["volume"] for m in members0))
        self.assertTrue(day0["time"].startswith("2024-06-01T00:00:00"))

    def test_unsupported_target(self) -> None:
        with self.assertRaises(mt4_tester.TesterFeedError):
            mt4_tester.resample_bars([], "H4")


class TestDecisionsCsv(unittest.TestCase):
    def test_columns_sorted_and_unix(self) -> None:
        decisions = [
            {
                "signal_time": "2024-06-02T18:00:00.000000000Z",
                "side": "long",
                "entry": 1.27,
                "stop": 1.264,
                "target": 1.282,
                "confidence": 0.5,
            },
            {
                "signal_time": "2024-06-01T09:00:00.000000000Z",
                "side": "short",
                "entry": 1.30,
                "stop": 1.306,
                "target": 1.288,
                "confidence": 0.6,
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decisions.csv"
            mt4_tester.write_decisions_csv(path, decisions)
            lines = path.read_text().strip().splitlines()
            self.assertEqual(lines[0], ",".join(mt4_tester.DECISION_FIELDS))
            # Sorted ascending by signal time -> the short (earlier) is first.
            first = lines[1].split(",")
            self.assertEqual(first[1], "short")
            self.assertEqual(int(first[0]), mt4_tester.rfc3339_to_unix(
                "2024-06-01T09:00:00.000000000Z"
            ))
            self.assertEqual(len(lines), 3)

    def test_empty_writes_header_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decisions.csv"
            mt4_tester.write_decisions_csv(path, [])
            lines = path.read_text().strip().splitlines()
            self.assertEqual(len(lines), 1)


class TestMtfDecisions(unittest.TestCase):
    """mtf_decisions emits every confirmed peak, ungated by open trades."""

    lookback = 40

    def _htf_for_ltf(self, ltf: list[dict]) -> list[dict]:
        from datetime import timedelta

        first = datetime.fromisoformat(str(ltf[0]["time"]).replace("Z", "+00:00"))
        htf_start = first - timedelta(days=self.lookback + 30)
        span = (len(ltf) // 24) + self.lookback + 60
        return _daily_bars(span, start=htf_start)

    def test_emits_all_peaks(self) -> None:
        ltf = _hourly_bars(55)
        # Two separate rollover peaks (fire then drop, twice).
        rsi_map = {
            str(ltf[41]["time"]): 24.0,
            str(ltf[42]["time"]): 55.0,  # rollover -> confirm peak #1 at 41
            str(ltf[48]["time"]): 26.0,
            str(ltf[49]["time"]): 55.0,  # rollover -> confirm peak #2 at 48
        }
        htf = self._htf_for_ltf(ltf)
        decisions = mtf_decisions(
            htf,
            ltf,
            _goal(),
            lookback=self.lookback,
            start_index=self.lookback - 1,
            htf_classify_fn=_htf_stub,
            ltf_classify_fn=_ltf_stub_factory(rsi_map),
        )
        self.assertEqual(len(decisions), 2)
        t41 = str(ltf[41]["time"])
        t48 = str(ltf[48]["time"])
        self.assertEqual(decisions[0]["signal_time"], t41)
        self.assertEqual(decisions[1]["signal_time"], t48)
        for d in decisions:
            self.assertEqual(d["side"], "long")
            self.assertLess(d["stop"], d["entry"])
            self.assertGreater(d["target"], d["entry"])

    def test_unconfirmed_peak_flushed_at_end(self) -> None:
        ltf = _hourly_bars(50)
        rsi_map = {str(ltf[49]["time"]): 22.0}  # peak on last bar, never rolls
        htf = self._htf_for_ltf(ltf)
        decisions = mtf_decisions(
            htf,
            ltf,
            _goal(),
            lookback=self.lookback,
            start_index=self.lookback - 1,
            htf_classify_fn=_htf_stub,
            ltf_classify_fn=_ltf_stub_factory(rsi_map),
        )
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["signal_time"], str(ltf[49]["time"]))


if __name__ == "__main__":
    unittest.main()
