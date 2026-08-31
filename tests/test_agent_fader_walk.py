"""Unit tests for the causal Ch.13 Fader decision feed (no network)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agent.fader_walk import fader_decisions
from agent.schema import Goal
from app import regime_walk


def _goal(**kwargs) -> Goal:
    data = {
        "instrument": "EUR_USD",
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


def _hourly_bars(n: int) -> list[dict]:
    origin = datetime(2024, 6, 1, tzinfo=timezone.utc)
    out: list[dict] = []
    for i in range(n):
        close = 1.10
        out.append(
            {
                "time": _iso(origin + timedelta(hours=i)),
                "open": close,
                "high": close + 0.001,
                "low": close - 0.001,
                "close": close,
                "volume": 100,
                "complete": True,
            }
        )
    return out


def _daily_bars(n: int) -> list[dict]:
    origin = datetime(2024, 4, 1, tzinfo=timezone.utc)
    out: list[dict] = []
    for i in range(n):
        close = 1.10
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


def _htf_ok() -> dict:
    return {
        "granularity": "D",
        "regime": "range",
        "direction": None,
        "trend_waning": False,
        "allowed_play_classes": ["fade_range"],
        "confidence": 0.5,
        "last_close": 1.095,
        "snapshot": {
            "adx": {"adx": 15.0, "rising": False},
            "prior_day": {"high": 1.1000, "low": 1.0900},
        },
    }


def _ltf_quiet() -> dict:
    return {
        "granularity": "H1",
        "last_close": 1.095,
        "snapshot": {
            "last_close": 1.095,
            "last_high": 1.096,
            "last_low": 1.094,
        },
    }


def _ltf_long_probe() -> dict:
    return {
        "granularity": "H1",
        "last_close": 1.0910,
        "snapshot": {
            "last_close": 1.0910,
            "last_high": 1.0920,
            "last_low": 1.0880,
        },
    }


class TestFaderDecisions(unittest.TestCase):
    def setUp(self) -> None:
        self.lookback = 40

    def test_first_fire_emits_one_decision(self) -> None:
        htf = _daily_bars(80)
        ltf = _hourly_bars(80)
        fire_t = str(ltf[50]["time"])

        def htf_fn(_window: list) -> dict:
            return _htf_ok()

        def ltf_fn(window: list) -> dict:
            t = str(window[-1]["time"])
            if t == fire_t:
                return _ltf_long_probe()
            return _ltf_quiet()

        decisions = fader_decisions(
            htf,
            ltf,
            _goal(),
            lookback=self.lookback,
            start_index=self.lookback - 1,
            htf_classify_fn=htf_fn,
            ltf_classify_fn=ltf_fn,
        )
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["signal_time"], fire_t)
        self.assertEqual(decisions[0]["side"], "long")
        self.assertEqual(decisions[0]["play_class"], "fade_range")
        self.assertLess(decisions[0]["stop"], decisions[0]["entry"])

    def test_no_probe_emits_nothing(self) -> None:
        decisions = fader_decisions(
            _daily_bars(50),
            _hourly_bars(80),
            _goal(),
            lookback=self.lookback,
            start_index=self.lookback - 1,
            htf_classify_fn=lambda _w: _htf_ok(),
            ltf_classify_fn=lambda _w: _ltf_quiet(),
        )
        self.assertEqual(decisions, [])

    def test_lookback_guard(self) -> None:
        with self.assertRaises(regime_walk.WalkError):
            fader_decisions(
                _daily_bars(50),
                _hourly_bars(80),
                _goal(),
                lookback=self.lookback,
                start_index=5,
                htf_classify_fn=lambda _w: _htf_ok(),
                ltf_classify_fn=lambda _w: _ltf_quiet(),
            )


if __name__ == "__main__":
    unittest.main()
