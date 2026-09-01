"""Unit tests for generic event_walk (Ch.14/16 one-shot), no network."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agent.event_walk import event_decisions, walk_event
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


def _daily_bars(n: int) -> list[dict]:
    origin = datetime(2024, 1, 1, tzinfo=timezone.utc)
    out: list[dict] = []
    for i in range(n):
        out.append(
            {
                "time": _iso(origin + timedelta(days=i)),
                "open": 1.10,
                "high": 1.11,
                "low": 1.09,
                "close": 1.10,
                "volume": 1000,
                "complete": True,
            }
        )
    return out


def _po_fire(last_close: float = 1.120) -> dict:
    return {
        "granularity": "D",
        "regime": "trend",
        "direction": "up",
        "trend_waning": False,
        "allowed_play_classes": ["join_trend"],
        "confidence": 0.6,
        "last_close": last_close,
        "snapshot": {
            "last_close": last_close,
            "last_low": 1.100,
            "last_high": 1.125,
            "ma_perfect_order": "up",
            "ma_perfect_order_age": 5,
            "sma": {"20": 1.090},
            "adx": {"adx": 25.0, "rising": True},
        },
    }


def _po_quiet() -> dict:
    snap = _po_fire()
    snap["snapshot"]["ma_perfect_order_age"] = 8
    return snap


def _b20_fire() -> dict:
    return {
        "granularity": "D",
        "regime": "trend",
        "direction": "up",
        "trend_waning": False,
        "allowed_play_classes": ["join_trend"],
        "confidence": 0.6,
        "last_close": 1.160,
        "snapshot": {
            "last_close": 1.160,
            "breakout_20": {
                "period": 20,
                "high_20": 1.150,
                "low_20": 1.050,
                "side": "long",
                "extreme_bars_ago": 3,
                "pullback_bars": 2,
                "rebreak": True,
            },
        },
    }


def _b20_first_touch() -> dict:
    out = _b20_fire()
    out["snapshot"]["breakout_20"]["rebreak"] = False
    out["snapshot"]["breakout_20"]["pullback_bars"] = 0
    return out


class TestEventDecisions(unittest.TestCase):
    def setUp(self) -> None:
        self.lookback = 40

    def test_perfect_order_pulse_once(self) -> None:
        bars = _daily_bars(60)
        t_fire = str(bars[45]["time"])

        def classify(window: list) -> dict:
            if str(window[-1]["time"]) == t_fire:
                return _po_fire()
            return _po_quiet()

        decisions = event_decisions(
            bars,
            _goal(),
            "perfect_order",
            lookback=self.lookback,
            start_index=self.lookback - 1,
            classify_fn=classify,
        )
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["signal_time"], t_fire)
        self.assertEqual(decisions[0]["side"], "long")

    def test_breakout20_skips_first_touch(self) -> None:
        bars = _daily_bars(60)

        def classify(_window: list) -> dict:
            return _b20_first_touch()

        decisions = event_decisions(
            bars,
            _goal(),
            "breakout20",
            lookback=self.lookback,
            start_index=self.lookback - 1,
            classify_fn=classify,
        )
        self.assertEqual(decisions, [])

    def test_breakout20_rebreak(self) -> None:
        bars = _daily_bars(60)
        t_fire = str(bars[50]["time"])

        def classify(window: list) -> dict:
            if str(window[-1]["time"]) == t_fire:
                return _b20_fire()
            return _b20_first_touch()

        decisions = event_decisions(
            bars,
            _goal(),
            "breakout20",
            lookback=self.lookback,
            start_index=self.lookback - 1,
            classify_fn=classify,
        )
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["side"], "long")

    def test_unknown_engine(self) -> None:
        with self.assertRaises(regime_walk.WalkError):
            event_decisions(_daily_bars(50), _goal(), "nope", lookback=40)


def _with_spread(bars: list[dict], half: float = 0.0002) -> list[dict]:
    out: list[dict] = []
    for b in bars:
        nb = dict(b)
        nb["bid"] = {
            "o": float(b["open"]) - half,
            "h": float(b["high"]) - half,
            "l": float(b["low"]) - half,
            "c": float(b["close"]) - half,
        }
        nb["ask"] = {
            "o": float(b["open"]) + half,
            "h": float(b["high"]) + half,
            "l": float(b["low"]) + half,
            "c": float(b["close"]) + half,
        }
        out.append(nb)
    return out


class TestWalkEventRest(unittest.TestCase):
    def test_fill_is_next_bar_ask(self) -> None:
        bars = _with_spread(_daily_bars(60))
        t_fire = str(bars[45]["time"])

        def classify(window: list) -> dict:
            if str(window[-1]["time"]) == t_fire:
                return _po_fire()
            return _po_quiet()

        result = walk_event(
            bars,
            _goal(fill_mode="rest"),
            "perfect_order",
            lookback=40,
            start_index=39,
            classify_fn=classify,
        )
        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertEqual(trade.entry_index, 46)
        self.assertAlmostEqual(trade.entry, bars[46]["ask"]["o"])
        self.assertIsNotNone(trade.units)
        self.assertGreaterEqual(trade.units, 1)


if __name__ == "__main__":
    unittest.main()
