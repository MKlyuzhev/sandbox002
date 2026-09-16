"""Unit tests for the causal regime-change walk (no network)."""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from agent import regime_change_walk, walk_jobs
from agent.regime_change_walk import walk_regime_change
from agent.schema import Goal, WalkResult


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")


def _flat_bars(n: int = 60) -> list[dict]:
    origin = datetime(2024, 1, 1, tzinfo=timezone.utc)
    out: list[dict] = []
    for i in range(n):
        out.append(
            {
                "time": _iso(origin + timedelta(days=i)),
                "open": 1.2000,
                "high": 1.20005,
                "low": 1.19995,
                "close": 1.2000,
                "volume": 1000,
                "complete": True,
            }
        )
    return out


def _flat_ba_bars(n: int = 60, spread: float = 0.0001) -> list[dict]:
    """Flat bars carrying bid/ask OHLC for rest-fill walks."""
    bars = _flat_bars(n)
    half = spread / 2.0
    for b in bars:
        o, h, l, c = b["open"], b["high"], b["low"], b["close"]
        b["bid"] = {"o": o - half, "h": h - half, "l": l - half, "c": c - half}
        b["ask"] = {"o": o + half, "h": h + half, "l": l + half, "c": c + half}
    return bars


def _goal(**kwargs) -> Goal:
    data = {
        "instrument": "EUR_USD",
        "granularity": "D",
        "balance": 10_000.0,
        "risk_fraction": 0.02,
    }
    data.update(kwargs)
    return Goal(**data)


def _confirmed_up_detect(sentinel_time: str):
    def fake_detect(window, analysis=None, *, instrument=None):
        if window[-1].get("time") == sentinel_time:
            return {
                "state": "confirmed",
                "score": 0.7,
                "direction_to": "up",
                "evidence": [
                    {
                        "signal": "trendline_break_valid",
                        "stage": "confirming",
                        "weight": 0.2,
                        "citation": {"source": "murphy-digital", "chunk_index": 47},
                    }
                ],
                "citations": [{"source": "murphy-digital", "chunk_index": 47}],
            }
        return {
            "state": "stable",
            "score": 0.0,
            "direction_to": None,
            "evidence": [],
            "citations": [],
        }

    return fake_detect


class TestDetectionReport(unittest.TestCase):
    def test_flat_no_trades(self) -> None:
        result, report = walk_regime_change(
            _flat_bars(), _goal(), lookback=40, start_index=39, journal=None
        )
        self.assertIsInstance(result, WalkResult)
        self.assertEqual(len(result.trades), 0)
        for key in (
            "detections",
            "precision",
            "recall",
            "flip_count",
            "state_counts",
            "step_count",
            "episodes",
        ):
            self.assertIn(key, report)
        self.assertGreater(report["step_count"], 0)


class TestPaperTicketFire(unittest.TestCase):
    def test_confirmed_first_fire_opens_trade(self) -> None:
        bars = _flat_bars(60)
        sentinel_time = bars[45]["time"]
        # A confirming bar (index 46) that trades up through the 2R target.
        bars[46] = {**bars[46], "high": 1.2025, "low": 1.2000, "close": 1.2020}

        with mock.patch.object(
            regime_change_walk.regime_change, "detect", _confirmed_up_detect(sentinel_time)
        ):
            result, _ = walk_regime_change(
                bars, _goal(), lookback=40, start_index=39, journal=None
            )
        self.assertGreaterEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertEqual(trade.side, "long")
        self.assertEqual(trade.entry_index, 45)
        self.assertIn("trendline_break_valid", trade.reasons)
        self.assertIn(trade.exit_status, ("target", "window_end", "stop"))

    def test_rest_fill_next_bar_ask_open(self) -> None:
        bars = _flat_ba_bars(60)
        sentinel_time = bars[45]["time"]
        with mock.patch.object(
            regime_change_walk.regime_change, "detect", _confirmed_up_detect(sentinel_time)
        ):
            result, _ = walk_regime_change(
                bars,
                _goal(fill_mode="rest"),
                lookback=40,
                start_index=39,
                journal=None,
            )
        self.assertGreaterEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertEqual(trade.side, "long")
        # Rest fills at the NEXT bar's ask open (index 46), with integer units.
        self.assertEqual(trade.entry_index, 46)
        self.assertIsNotNone(trade.units)
        self.assertGreaterEqual(trade.units, 1)
        self.assertAlmostEqual(trade.entry, bars[46]["ask"]["o"], places=6)


class TestWalkJobsIntegration(unittest.TestCase):
    def test_execute_walk_regime_change(self) -> None:
        bars = _flat_bars(60)

        async def fake_fetch(instrument, granularity, from_time, to_time, lookback, with_ba=False):
            return bars

        result, meta = asyncio.run(
            walk_jobs.execute_walk(
                "regime_change",
                "EUR_USD",
                bars[39]["time"],
                bars[-1]["time"],
                granularity="D",
                lookback=40,
                no_journal=True,
                fetch_fn=fake_fetch,
            )
        )
        self.assertIsInstance(result, WalkResult)
        self.assertEqual(meta["engine"], "regime_change")
        self.assertIn("detection", meta)
        payload = walk_jobs.compact_walk_payload(result, meta)
        self.assertNotIn("bars", payload)
        self.assertIn("detection", payload)

    def test_unknown_kind_raises(self) -> None:
        async def fake_fetch(*a, **k):
            return _flat_bars()

        with self.assertRaises(walk_jobs.WalkJobError):
            asyncio.run(
                walk_jobs.execute_walk(
                    "bogus", "EUR_USD", "2024-01-01T00:00:00Z", "2024-03-01T00:00:00Z",
                    fetch_fn=fake_fetch,
                )
            )


class TestEarlyWarningDetect(unittest.TestCase):
    def test_first_fire_post_vs_score(self) -> None:
        bars = _flat_bars(60)
        fire_t = bars[45]["time"]
        for b in bars[50:]:
            b["close"] = 1.2100
            b["high"] = 1.2105
            b["open"] = 1.2100

        def fake_detect(window, analysis=None, *, instrument=None):
            if window[-1].get("time") == fire_t:
                return {
                    "state": "early_warning",
                    "score": 0.25,
                    "direction_from": "up",
                    "direction_to": "down",
                }
            return {
                "state": "stable",
                "score": 0.0,
                "direction_from": None,
                "direction_to": None,
            }

        def fake_classify(window):
            t = str(window[-1].get("time") or "")
            i = next(idx for idx, b in enumerate(bars) if b["time"] == t)
            if i >= 50:
                return {"regime": "range", "direction": None, "confidence": 0.4}
            return {"regime": "trend", "direction": "up", "confidence": 0.7}

        with mock.patch.object(regime_change_walk.regime_change, "detect", fake_detect):
            out = regime_change_walk.walk_early_warning(
                bars,
                "EUR_USD",
                lookback=40,
                start_index=39,
                classify_fn=fake_classify,
                horizons=(5, 10),
            )
        self.assertEqual(out["event_count"], 1)
        ev = out["events"][0]
        self.assertEqual(ev["score"], 0.25)
        self.assertEqual(ev["direction_to"], "down")
        h5 = ev["post"]["5"]
        self.assertTrue(h5["flip"])
        self.assertEqual(h5["lead_time"], 5)
        self.assertFalse(h5["dir_hit"])
        self.assertLess(h5["signed_pips"], 0)


if __name__ == "__main__":
    unittest.main()
