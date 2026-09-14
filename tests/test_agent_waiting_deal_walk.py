"""Unit tests for the causal Ch.11 Waiting-for-the-Deal walk (no network)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agent.schema import Goal
from agent.waiting_deal_walk import waiting_deal_decisions, walk_waiting_deal


def _goal(**kwargs) -> Goal:
    data = {
        "instrument": "GBP_USD",
        "granularity": "D",
        "ltf_granularity": "M15",
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


def _daily_bars(n: int) -> list[dict]:
    origin = datetime(2024, 6, 1, tzinfo=timezone.utc)
    out: list[dict] = []
    for i in range(n):
        close = 1.27
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


def _m15_setup() -> list[dict]:
    """Quiet M15 bars, then 15 Jul 2024 hunt-then-reverse."""
    origin = datetime(2024, 7, 14, 12, 0, tzinfo=timezone.utc)
    out: list[dict] = []
    for i in range(40):
        dt = origin + timedelta(minutes=15 * i)
        out.append(
            {
                "time": _iso(dt),
                "open": 1.2710,
                "high": 1.2712,
                "low": 1.2708,
                "close": 1.2710,
                "volume": 10,
                "complete": True,
            }
        )
    day = datetime(2024, 7, 15, 6, 0, tzinfo=timezone.utc)
    extra = [
        (day, 1.2720, 1.2705),
        (day.replace(minute=15), 1.2718, 1.2700),
        (day.replace(minute=30), 1.2715, 1.2702),
        (day.replace(minute=45), 1.2716, 1.2704),
        (day.replace(hour=7), 1.2755, 1.2715),
        (day.replace(hour=8), 1.2710, 1.2685),
        (day.replace(hour=9), 1.2695, 1.2680),
    ]
    for dt, high, low in extra:
        mid = (high + low) / 2.0
        out.append(
            {
                "time": _iso(dt),
                "open": mid,
                "high": high,
                "low": low,
                "close": mid,
                "volume": 10,
                "complete": True,
            }
        )
    return out


def _htf_ok() -> dict:
    return {
        "granularity": "D",
        "regime": "range",
        "direction": None,
        "trend_waning": False,
        "allowed_play_classes": ["fade_range"],
        "confidence": 0.5,
        "last_close": 1.27,
        "snapshot": {},
    }


class TestWaitingDealDecisions(unittest.TestCase):
    def test_first_fire_emits_one_short(self) -> None:
        ltf = _m15_setup()
        reverse_t = str(ltf[-2]["time"])  # 08:00 hunt reverse; last is 09:00
        decisions = waiting_deal_decisions(
            _daily_bars(80),
            ltf,
            _goal(),
            lookback=40,
            start_index=0,
            htf_classify_fn=lambda _w: _htf_ok(),
        )
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["side"], "short")
        self.assertEqual(decisions[0]["signal_time"], reverse_t)
        self.assertGreater(decisions[0]["stop"], decisions[0]["entry"])

    def test_walk_one_trade(self) -> None:
        result = walk_waiting_deal(
            _daily_bars(80),
            _m15_setup(),
            _goal(),
            lookback=40,
            start_index=0,
            htf_classify_fn=lambda _w: _htf_ok(),
        )
        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].side, "short")


if __name__ == "__main__":
    unittest.main()
