"""Unit tests for walk candle fetch (no live OANDA)."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from app import oanda_client, regime_walk
from app.walk_fetch import fetch_walk_bars


def _mba_candle(ts: str) -> dict:
    return {
        "time": ts,
        "complete": True,
        "volume": 1,
        "mid": {"o": "1.10", "h": "1.11", "l": "1.09", "c": "1.10"},
        "bid": {"o": "1.0998", "h": "1.1098", "l": "1.0898", "c": "1.0998"},
        "ask": {"o": "1.1002", "h": "1.1102", "l": "1.0902", "c": "1.1002"},
    }


def _mid_candle(ts: str) -> dict:
    return {
        "time": ts,
        "complete": True,
        "volume": 1,
        "mid": {"o": "1.10", "h": "1.11", "l": "1.09", "c": "1.10"},
    }


class TestFetchWalkBars(unittest.TestCase):
    def test_close_mode_requests_mid(self) -> None:
        prices: list[str] = []

        async def fake_get_candles(*_a, **kwargs):
            prices.append(kwargs.get("price", ""))
            ts = kwargs.get("to_time") or kwargs.get("from_time") or "2024-01-01T00:00:00.000000000Z"
            return {"candles": [_mid_candle(ts)]}

        with patch.object(oanda_client, "get_candles", fake_get_candles):
            bars = asyncio.run(
                fetch_walk_bars(
                    "EUR_USD",
                    "D",
                    "2024-02-01T00:00:00Z",
                    "2024-02-02T00:00:00Z",
                    lookback=40,
                    with_ba=False,
                )
            )
        self.assertEqual(prices, ["M", "M"])
        self.assertTrue(bars)
        self.assertNotIn("bid", bars[0])

    def test_rest_mode_requests_mba_and_attaches_ba(self) -> None:
        prices: list[str] = []

        async def fake_get_candles(*_a, **kwargs):
            prices.append(kwargs.get("price", ""))
            ts = kwargs.get("to_time") or kwargs.get("from_time") or "2024-01-01T00:00:00.000000000Z"
            return {"candles": [_mba_candle(ts)]}

        with patch.object(oanda_client, "get_candles", fake_get_candles):
            bars = asyncio.run(
                fetch_walk_bars(
                    "EUR_USD",
                    "D",
                    "2024-02-01T00:00:00Z",
                    "2024-02-02T00:00:00Z",
                    lookback=40,
                    with_ba=True,
                )
            )
        self.assertEqual(prices, ["MBA", "MBA"])
        self.assertTrue(bars)
        self.assertIn("bid", bars[0])
        self.assertIn("ask", bars[0])

    def test_rest_mode_missing_ba_raises(self) -> None:
        async def fake_get_candles(*_a, **kwargs):
            ts = kwargs.get("to_time") or kwargs.get("from_time") or "2024-01-01T00:00:00.000000000Z"
            return {"candles": [_mid_candle(ts)]}

        with patch.object(oanda_client, "get_candles", fake_get_candles):
            with self.assertRaises(regime_walk.WalkError):
                asyncio.run(
                    fetch_walk_bars(
                        "EUR_USD",
                        "D",
                        "2024-02-01T00:00:00Z",
                        "2024-02-02T00:00:00Z",
                        lookback=40,
                        with_ba=True,
                    )
                )


if __name__ == "__main__":
    unittest.main()
