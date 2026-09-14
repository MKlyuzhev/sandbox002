"""Unit tests for the Ch.11 Waiting-for-the-Deal entry engine (no network)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agent.engines import waiting_deal
from agent.engines.base import EngineContext
from agent.schema import Goal
from app import risk as risk_lib


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")


def _bar(dt: datetime, *, high: float, low: float) -> dict:
    mid = (high + low) / 2.0
    return {
        "time": _iso(dt),
        "open": mid,
        "high": high,
        "low": low,
        "close": mid,
        "volume": 10,
        "complete": True,
    }


def _deal_bars() -> list[dict]:
    origin = datetime(2024, 7, 15, 6, 0, tzinfo=timezone.utc)
    return [
        _bar(origin, high=1.2720, low=1.2705),
        _bar(origin.replace(minute=15), high=1.2718, low=1.2700),
        _bar(origin.replace(minute=30), high=1.2715, low=1.2702),
        _bar(origin.replace(minute=45), high=1.2716, low=1.2704),
        _bar(origin.replace(hour=7), high=1.2755, low=1.2715),
        _bar(origin.replace(hour=8), high=1.2710, low=1.2685),
    ]


def _htf(
    *,
    waning: bool = False,
    plays: list[str] | None = None,
    confidence: float = 0.5,
) -> dict:
    return {
        "granularity": "D",
        "regime": "range",
        "direction": None,
        "trend_waning": waning,
        "allowed_play_classes": plays if plays is not None else ["fade_range"],
        "confidence": confidence,
        "snapshot": {},
    }


class TestWaitingDealSignal(unittest.TestCase):
    def test_short_after_upside_hunt(self) -> None:
        result = waiting_deal.waiting_deal_signal(
            _htf(), _deal_bars(), "GBP_USD", ltf_granularity="M15"
        )
        self.assertEqual(result["signal"], "short")
        self.assertEqual(result["play_class"], "fade_range")
        ticket = result["ticket"]
        assert ticket is not None
        self.assertEqual(ticket["side"], "short")
        self.assertGreater(ticket["stop"], ticket["entry"])
        self.assertGreaterEqual(
            risk_lib.r_multiple(ticket["entry"], ticket["stop"], ticket["target"]),
            2.0,
        )
        self.assertEqual(result["chapter"], 11)
        self.assertTrue(any(c["source"] == "lien-fx" for c in result["citations"]))
        self.assertIn("news filter unavailable", result["note"])

    def test_join_trend_is_none(self) -> None:
        result = waiting_deal.waiting_deal_signal(
            _htf(plays=["join_trend"]), _deal_bars(), "GBP_USD"
        )
        self.assertEqual(result["signal"], "none")
        self.assertIn("regime allows", result["reason"])

    def test_trend_waning_is_none(self) -> None:
        result = waiting_deal.waiting_deal_signal(
            _htf(waning=True), _deal_bars(), "GBP_USD"
        )
        self.assertEqual(result["signal"], "none")
        self.assertIn("trend_waning", result["reason"])

    def test_missing_ltf_is_none(self) -> None:
        result = waiting_deal.waiting_deal_signal(_htf(), None, "GBP_USD")
        self.assertEqual(result["signal"], "none")
        self.assertIn("missing lower-TF", result["reason"])

    def test_mixed_uses_breakout_watch(self) -> None:
        result = waiting_deal.waiting_deal_signal(
            _htf(plays=["breakout_watch"]), _deal_bars(), "GBP_USD"
        )
        self.assertEqual(result["signal"], "short")
        self.assertEqual(result["play_class"], "breakout_watch")


class TestWaitingDealEngine(unittest.TestCase):
    def test_granularities_remap_h1_to_m15(self) -> None:
        eng = waiting_deal.WaitingDealEngine()
        self.assertEqual(
            eng.granularities(Goal(granularity="D", ltf_granularity="H1")),
            ("D", "M15"),
        )
        self.assertEqual(
            eng.granularities(Goal(granularity="D", ltf_granularity="M5")),
            ("D", "M5"),
        )

    def test_engine_reads_ohlc_from_analysis(self) -> None:
        ctx = EngineContext(
            instrument="GBP_USD",
            goal=Goal(granularity="D", ltf_granularity="M15"),
            analyses={
                "D": _htf(),
                "M15": {"ohlc": _deal_bars()},
            },
        )
        result = waiting_deal.WaitingDealEngine().signal(ctx)
        self.assertEqual(result.signal, "short")
        self.assertTrue(result.firing)

    def test_confidence_zero_when_not_firing(self) -> None:
        self.assertEqual(waiting_deal.signal_confidence("none", _htf(), None), 0.0)


if __name__ == "__main__":
    unittest.main()
