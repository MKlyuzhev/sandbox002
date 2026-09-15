"""Unit tests for the Ch.10 double-zeros entry engine (no network)."""

from __future__ import annotations

import unittest

from agent.engines import double_zeros
from agent.engines.base import EngineContext
from agent.schema import Goal
from app import risk as risk_lib


def _bar(close: float, *, i: int = 0) -> dict:
    return {
        "time": f"2024-07-15T12:{i:02d}:00.000000000Z",
        "open": close,
        "high": close + 0.0002,
        "low": close - 0.0002,
        "close": close,
        "volume": 10,
        "complete": True,
    }


def _sma_then(last_close: float, *, base: float, n: int = 19) -> list[dict]:
    bars = [_bar(base, i=min(i, 59)) for i in range(n)]
    bars.append(_bar(last_close, i=0))
    return bars


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


class TestDoubleZerosSignal(unittest.TestCase):
    def test_long_12_pips_above_figure(self) -> None:
        # 1.1112 is 12 pips above 1.1100; SMA of 1.20 keeps close below SMA.
        result = double_zeros.double_zeros_signal(
            _htf(), _sma_then(1.1112, base=1.20), "GBP_USD"
        )
        self.assertEqual(result["signal"], "long")
        self.assertEqual(result["play_class"], "fade_range")
        ticket = result["ticket"]
        assert ticket is not None
        self.assertEqual(ticket["side"], "long")
        self.assertLess(ticket["stop"], ticket["entry"])
        self.assertAlmostEqual(ticket["stop"], 1.1080, places=4)
        self.assertGreaterEqual(
            risk_lib.r_multiple(ticket["entry"], ticket["stop"], ticket["target"]),
            2.0,
        )
        self.assertEqual(result["chapter"], 10)
        self.assertEqual(result["setup"]["distance_pips"], 12.0)
        self.assertIn("news filter unavailable", result["note"])

    def test_short_12_pips_below_figure(self) -> None:
        result = double_zeros.double_zeros_signal(
            _htf(), _sma_then(1.1088, base=1.10), "GBP_USD"
        )
        self.assertEqual(result["signal"], "short")
        ticket = result["ticket"]
        assert ticket is not None
        self.assertGreater(ticket["stop"], ticket["entry"])
        self.assertAlmostEqual(ticket["stop"], 1.1120, places=4)

    def test_jpy_pip_size(self) -> None:
        bars = [
            {
                "time": f"2024-07-15T12:{i:02d}:00.000000000Z",
                "open": 119.5,
                "high": 119.6,
                "low": 119.4,
                "close": 119.5,
                "complete": True,
            }
            for i in range(19)
        ]
        bars.append(
            {
                "time": "2024-07-15T13:00:00.000000000Z",
                "open": 118.12,
                "high": 118.14,
                "low": 118.10,
                "close": 118.12,
                "complete": True,
            }
        )
        result = double_zeros.double_zeros_signal(_htf(), bars, "USD_JPY")
        self.assertEqual(result["signal"], "long")
        self.assertAlmostEqual(result["setup"]["figure"], 118.0, places=2)
        self.assertAlmostEqual(result["ticket"]["stop"], 117.80, places=2)

    def test_sma_blocks_long_above_average(self) -> None:
        result = double_zeros.double_zeros_signal(
            _htf(), _sma_then(1.1112, base=1.10), "GBP_USD"
        )
        self.assertEqual(result["signal"], "none")

    def test_join_trend_is_none(self) -> None:
        result = double_zeros.double_zeros_signal(
            _htf(plays=["join_trend"]),
            _sma_then(1.1112, base=1.20),
            "GBP_USD",
        )
        self.assertEqual(result["signal"], "none")
        self.assertIn("fade_range", result["reason"])

    def test_trend_waning_is_none(self) -> None:
        result = double_zeros.double_zeros_signal(
            _htf(waning=True), _sma_then(1.1112, base=1.20), "GBP_USD"
        )
        self.assertEqual(result["signal"], "none")
        self.assertIn("trend_waning", result["reason"])

    def test_missing_ltf_is_none(self) -> None:
        result = double_zeros.double_zeros_signal(_htf(), None, "GBP_USD")
        self.assertEqual(result["signal"], "none")
        self.assertIn("missing lower-TF", result["reason"])

    def test_too_far_from_figure(self) -> None:
        result = double_zeros.double_zeros_signal(
            _htf(), _sma_then(1.1125, base=1.20), "GBP_USD"
        )
        self.assertEqual(result["signal"], "none")


class TestDoubleZerosEngine(unittest.TestCase):
    def test_granularities_remap_h1_to_m15(self) -> None:
        eng = double_zeros.DoubleZerosEngine()
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
                "M15": {"ohlc": _sma_then(1.1112, base=1.20)},
            },
        )
        result = double_zeros.DoubleZerosEngine().signal(ctx)
        self.assertEqual(result.signal, "long")
        self.assertTrue(result.firing)

    def test_confidence_zero_when_not_firing(self) -> None:
        self.assertEqual(double_zeros.signal_confidence("none", _htf(), None), 0.0)


if __name__ == "__main__":
    unittest.main()
