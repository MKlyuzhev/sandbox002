"""Unit tests for the Ch.13 Fader entry engine (no network)."""

from __future__ import annotations

import unittest

from agent.engines import fader
from app import risk as risk_lib


def _htf(
    *,
    adx: float = 15.0,
    prior_high: float = 1.1000,
    prior_low: float = 1.0900,
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
        "snapshot": {
            "adx": {"adx": adx, "rising": False},
            "prior_day": {"high": prior_high, "low": prior_low},
        },
    }


def _ltf(
    *,
    last_close: float,
    last_high: float,
    last_low: float,
) -> dict:
    return {
        "granularity": "H1",
        "last_close": last_close,
        "snapshot": {
            "last_close": last_close,
            "last_high": last_high,
            "last_low": last_low,
        },
    }


class TestFaderSignal(unittest.TestCase):
    def test_failed_breakdown_is_long(self) -> None:
        # Prior low 1.0900; 15 pips = 0.0015 → probe to 1.0885; close back at 1.0910.
        result = fader.fader_signal(
            _htf(),
            _ltf(last_close=1.0910, last_high=1.0920, last_low=1.0880),
            "EUR_USD",
        )
        self.assertEqual(result["signal"], "long")
        self.assertEqual(result["play_class"], "fade_range")
        ticket = result["ticket"]
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["side"], "long")
        self.assertEqual(ticket["stop_name"], "probe_low")
        self.assertLess(ticket["stop"], ticket["entry"])
        self.assertGreaterEqual(
            risk_lib.r_multiple(ticket["entry"], ticket["stop"], ticket["target"]),
            2.0,
        )
        self.assertEqual(result["chapter"], 13)
        self.assertTrue(any(c["source"] == "lien-fx" for c in result["citations"]))

    def test_failed_breakout_is_short(self) -> None:
        result = fader.fader_signal(
            _htf(),
            _ltf(last_close=1.0990, last_high=1.1020, last_low=1.0980),
            "EUR_USD",
        )
        self.assertEqual(result["signal"], "short")
        ticket = result["ticket"]
        assert ticket is not None
        self.assertEqual(ticket["stop_name"], "probe_high")
        self.assertGreater(ticket["stop"], ticket["entry"])
        self.assertGreaterEqual(
            risk_lib.r_multiple(ticket["entry"], ticket["stop"], ticket["target"]),
            2.0,
        )

    def test_probe_without_close_inside_is_none(self) -> None:
        # Probed below but closed still outside the prior low.
        result = fader.fader_signal(
            _htf(),
            _ltf(last_close=1.0885, last_high=1.0895, last_low=1.0870),
            "EUR_USD",
        )
        self.assertEqual(result["signal"], "none")

    def test_adx_too_high_is_none(self) -> None:
        result = fader.fader_signal(
            _htf(adx=22.0),
            _ltf(last_close=1.0910, last_high=1.0920, last_low=1.0880),
            "EUR_USD",
        )
        self.assertEqual(result["signal"], "none")
        self.assertIn("ADX", result["reason"])

    def test_missing_ltf_is_none(self) -> None:
        result = fader.fader_signal(_htf(), None, "EUR_USD")
        self.assertEqual(result["signal"], "none")
        self.assertIn("missing lower-TF", result["reason"])

    def test_play_class_gate(self) -> None:
        result = fader.fader_signal(
            _htf(plays=["join_trend"]),
            _ltf(last_close=1.0910, last_high=1.0920, last_low=1.0880),
            "EUR_USD",
        )
        self.assertEqual(result["signal"], "none")
        self.assertIn("regime allows", result["reason"])

    def test_trend_waning_is_none(self) -> None:
        result = fader.fader_signal(
            _htf(waning=True),
            _ltf(last_close=1.0910, last_high=1.0920, last_low=1.0880),
            "EUR_USD",
        )
        self.assertEqual(result["signal"], "none")
        self.assertIn("trend_waning", result["reason"])


class TestFaderEngine(unittest.TestCase):
    def test_engine_does_not_fire_without_ltf(self) -> None:
        from agent.engines.base import EngineContext
        from agent.schema import Goal

        ctx = EngineContext(
            instrument="EUR_USD",
            goal=Goal(granularity="D", ltf_granularity="H1"),
            analyses={"D": _htf()},
        )
        result = fader.FaderEngine().signal(ctx)
        self.assertEqual(result.signal, "none")
        self.assertFalse(result.firing)

    def test_confidence_zero_when_not_firing(self) -> None:
        self.assertEqual(fader.signal_confidence("none", _htf(), None), 0.0)


if __name__ == "__main__":
    unittest.main()
