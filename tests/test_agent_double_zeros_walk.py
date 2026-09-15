"""Unit tests for the causal Ch.10 double-zeros walk (no network)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agent.double_zeros_walk import double_zeros_decisions, walk_double_zeros
from agent.schema import Goal


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
        close = 1.20
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
    origin = datetime(2024, 7, 14, 12, 0, tzinfo=timezone.utc)
    out: list[dict] = []
    for i in range(30):
        dt = origin + timedelta(minutes=15 * i)
        out.append(
            {
                "time": _iso(dt),
                "open": 1.2000,
                "high": 1.2002,
                "low": 1.1998,
                "close": 1.2000,
                "volume": 10,
                "complete": True,
            }
        )
    fire = origin + timedelta(minutes=15 * 30)
    out.append(
        {
            "time": _iso(fire),
            "open": 1.1112,
            "high": 1.1114,
            "low": 1.1110,
            "close": 1.1112,
            "volume": 10,
            "complete": True,
        }
    )
    out.append(
        {
            "time": _iso(fire + timedelta(minutes=15)),
            "open": 1.1112,
            "high": 1.1114,
            "low": 1.1110,
            "close": 1.1112,
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
        "last_close": 1.20,
        "snapshot": {},
    }


class TestDoubleZerosDecisions(unittest.TestCase):
    def test_first_fire_emits_one_long(self) -> None:
        ltf = _m15_setup()
        fire_t = str(ltf[-2]["time"])
        decisions = double_zeros_decisions(
            _daily_bars(80),
            ltf,
            _goal(),
            lookback=40,
            start_index=0,
            htf_classify_fn=lambda _w: _htf_ok(),
        )
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["side"], "long")
        self.assertEqual(decisions[0]["signal_time"], fire_t)
        self.assertLess(decisions[0]["stop"], decisions[0]["entry"])

    def test_walk_one_trade(self) -> None:
        result = walk_double_zeros(
            _daily_bars(80),
            _m15_setup(),
            _goal(),
            lookback=40,
            start_index=0,
            htf_classify_fn=lambda _w: _htf_ok(),
        )
        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].side, "long")


if __name__ == "__main__":
    unittest.main()
