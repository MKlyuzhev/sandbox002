"""Unit tests for the causal Ch.9 Double Bollinger decision feed (no network).

``dbb_decisions`` is pure over an injected ``classify_fn``, so tests drive a
stub classifier keyed by bar time to place a 1sigma cross at a known bar and
assert exactly one one-shot decision is emitted there.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agent.dbb_walk import dbb_decisions
from agent.schema import Goal
from app import regime_walk


def _goal(**kwargs) -> Goal:
    data = {
        "instrument": "EUR_USD",
        "granularity": "D",
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


def _daily_bars(n: int, *, base_close: float = 1.10) -> list[dict]:
    origin = datetime(2024, 1, 1, tzinfo=timezone.utc)
    out: list[dict] = []
    for i in range(n):
        close = base_close
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


def _dbb_block(zone: str, prev_zone: str, prev2_zone: str) -> dict:
    return {
        "period": 20,
        "upper_2": 1.110,
        "upper_1": 1.105,
        "mid": 1.100,
        "lower_1": 1.095,
        "lower_2": 1.090,
        "zone": zone,
        "prev_zone": prev_zone,
        "prev2_zone": prev2_zone,
    }


def _classify_factory(cross_by_time: dict[str, dict]):
    """Stub classifier: a cross spec at a given bar time, else a quiet range."""

    def _stub(window: list[dict]) -> dict:
        t = str(window[-1]["time"])
        spec = cross_by_time.get(t)
        if spec is None:
            return {
                "granularity": "D",
                "regime": "range",
                "direction": None,
                "trend_waning": False,
                "allowed_play_classes": ["fade_range"],
                "confidence": 0.4,
                "last_close": 1.100,
                "snapshot": {
                    "last_close": 1.100,
                    "double_bb": _dbb_block("range", "range", "range"),
                },
            }
        return {
            "granularity": "D",
            "regime": spec["regime"],
            "direction": spec.get("direction"),
            "trend_waning": False,
            "allowed_play_classes": spec["plays"],
            "confidence": 0.7,
            "last_close": spec["last_close"],
            "snapshot": {
                "last_close": spec["last_close"],
                "double_bb": spec["dbb"],
            },
        }

    return _stub


class TestDbbDecisions(unittest.TestCase):
    def setUp(self) -> None:
        self.lookback = 40

    def _run(self, cross_by_time: dict[str, dict], *, n: int = 60):
        bars = _daily_bars(n)
        return bars, dbb_decisions(
            bars,
            _goal(),
            lookback=self.lookback,
            start_index=self.lookback - 1,
            classify_fn=_classify_factory(cross_by_time),
        )

    def test_single_join_long_decision_at_cross_bar(self) -> None:
        bars = _daily_bars(60)
        t_cross = bars[45]["time"]
        spec = {
            "regime": "trend",
            "direction": "up",
            "plays": ["join_trend"],
            "last_close": 1.107,
            "dbb": _dbb_block("trend_up", "range", "range"),
        }
        _bars, decisions = self._run({str(t_cross): spec})
        self.assertEqual(len(decisions), 1)
        d = decisions[0]
        self.assertEqual(d["signal_time"], str(t_cross))
        self.assertEqual(d["side"], "long")
        self.assertEqual(d["play_class"], "join_trend")
        self.assertLess(d["stop"], d["entry"])
        self.assertGreater(d["target"], d["entry"])

    def test_fade_short_decision(self) -> None:
        bars = _daily_bars(60)
        t_cross = bars[50]["time"]
        spec = {
            "regime": "range",
            "direction": None,
            "plays": ["fade_range"],
            "last_close": 1.104,
            "dbb": _dbb_block("range", "trend_up", "trend_up"),
        }
        _bars, decisions = self._run({str(t_cross): spec})
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["side"], "short")
        self.assertEqual(decisions[0]["play_class"], "fade_range")
        self.assertGreater(decisions[0]["stop"], decisions[0]["entry"])

    def test_no_cross_emits_nothing(self) -> None:
        _bars, decisions = self._run({})
        self.assertEqual(decisions, [])

    def test_wrong_regime_gate_drops_decision(self) -> None:
        # A join breakout while the stub regime only allows fade_range: the
        # engine returns none and no decision is emitted.
        bars = _daily_bars(60)
        t_cross = bars[45]["time"]
        spec = {
            "regime": "range",
            "direction": None,
            "plays": ["fade_range"],
            "last_close": 1.107,
            "dbb": _dbb_block("trend_up", "range", "range"),
        }
        _bars, decisions = self._run({str(t_cross): spec})
        self.assertEqual(decisions, [])

    def test_lookback_guard(self) -> None:
        bars = _daily_bars(60)
        with self.assertRaises(regime_walk.WalkError):
            dbb_decisions(
                bars,
                _goal(),
                lookback=self.lookback,
                start_index=5,
                classify_fn=_classify_factory({}),
            )


if __name__ == "__main__":
    unittest.main()
