"""Unit tests for universe scan (no network)."""

from __future__ import annotations

import asyncio
import unittest

from agent.scan import (
    DEFAULT_UNIVERSE,
    MAX_INSTRUMENTS,
    ScanError,
    parse_instruments,
    scan_regimes,
)


def _row(
    instrument: str,
    *,
    regime: str = "trend",
    waning: bool = False,
    plays: list[str] | None = None,
) -> dict:
    return {
        "instrument": instrument,
        "regime": regime,
        "direction": "up",
        "trend_waning": waning,
        "allowed_play_classes": plays or ["join_trend"],
        "confidence": 0.6,
        "last_close": 1.1,
    }


class TestParseInstruments(unittest.TestCase):
    def test_empty_defaults(self) -> None:
        self.assertEqual(parse_instruments(""), list(DEFAULT_UNIVERSE))
        self.assertEqual(parse_instruments(None), list(DEFAULT_UNIVERSE))

    def test_csv(self) -> None:
        self.assertEqual(
            parse_instruments("EUR_USD, GBP_USD"),
            ["EUR_USD", "GBP_USD"],
        )


class TestScanRegimes(unittest.TestCase):
    def test_cap(self) -> None:
        names = [f"P{i}_USD" for i in range(MAX_INSTRUMENTS + 1)]

        async def boom(*_a):
            raise AssertionError("should not classify")

        with self.assertRaises(ScanError):
            asyncio.run(scan_regimes(names, classify_fn=boom))

    def test_drop_waning_and_play_class(self) -> None:
        canned = {
            "EUR_USD": _row("EUR_USD", plays=["join_trend"]),
            "GBP_USD": _row("GBP_USD", waning=True, plays=["join_trend"]),
            "AUD_USD": _row("AUD_USD", regime="range", plays=["fade_range"]),
        }

        async def classify(name, *_rest):
            return canned[name]

        out = asyncio.run(
            scan_regimes(
                "EUR_USD,GBP_USD,AUD_USD",
                drop_waning=True,
                play_class="join_trend",
                classify_fn=classify,
            )
        )
        self.assertEqual(out["kept"], ["EUR_USD"])
        reasons = {d["instrument"]: d["reason"] for d in out["dropped"]}
        self.assertEqual(reasons["GBP_USD"], "trend_waning")
        self.assertEqual(reasons["AUD_USD"], "play_class")
        self.assertEqual(len(out["rows"]), 3)

    def test_row_error_continues(self) -> None:
        async def classify(name, *_rest):
            if name == "BAD_USD":
                raise RuntimeError("fetch failed")
            return _row(name)

        out = asyncio.run(
            scan_regimes("EUR_USD,BAD_USD", drop_waning=False, classify_fn=classify)
        )
        self.assertEqual(out["kept"], ["EUR_USD"])
        self.assertEqual(out["dropped"][0]["reason"], "error")
        self.assertIn("fetch failed", out["rows"][1]["error"])


if __name__ == "__main__":
    unittest.main()
