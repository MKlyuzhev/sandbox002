"""Unit tests for OANDA candle paging (no network)."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from app import oanda_client


def _candle(ts: str) -> dict:
    return {
        "time": ts,
        "complete": True,
        "mid": {"o": "1.0", "h": "1.0", "l": "1.0", "c": "1.0"},
    }


def _hour(i: int) -> str:
    return f"2024-01-01T{i:02d}:00:00.000000000Z"


class _FakeGet:
    def __init__(self, responses: list) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def __call__(self, path: str, params: dict | None = None) -> dict:
        self.calls.append(dict(params or {}))
        if not self.responses:
            raise oanda_client.OandaError("OANDA GET unexpected extra call")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class TestCandlePaging(unittest.TestCase):
    def test_from_to_uses_single_request_when_ok(self) -> None:
        fake = _FakeGet(
            [{"instrument": "GBP_USD", "granularity": "H1", "candles": [_candle(_hour(0))]}]
        )
        with patch.object(oanda_client, "get", fake):
            payload = asyncio.run(
                oanda_client.get_candles(
                    "GBP_USD",
                    granularity="H1",
                    count=None,
                    from_time=_hour(0),
                    to_time=_hour(3),
                )
            )
        self.assertEqual(len(payload["candles"]), 1)
        self.assertNotIn("count", fake.calls[0])
        self.assertEqual(fake.calls[0]["from"], _hour(0))
        self.assertEqual(fake.calls[0]["to"], _hour(3))

    def test_from_to_pages_after_400(self) -> None:
        page1 = [_candle(_hour(i)) for i in range(3)]
        page2 = [_candle(_hour(i)) for i in range(3, 6)]
        fake = _FakeGet(
            [
                oanda_client.OandaError("OANDA GET failed (400): max candles"),
                {"instrument": "GBP_USD", "granularity": "H1", "candles": page1},
                {"instrument": "GBP_USD", "granularity": "H1", "candles": page2},
            ]
        )
        with patch.object(oanda_client, "get", fake):
            with patch.object(oanda_client, "MAX_CANDLES_PER_REQUEST", 3):
                payload = asyncio.run(
                    oanda_client.get_candles(
                        "GBP_USD",
                        granularity="H1",
                        count=None,
                        from_time=_hour(0),
                        to_time=_hour(4),
                    )
                )
        times = [c["time"] for c in payload["candles"]]
        self.assertEqual(times, [_hour(i) for i in range(5)])
        self.assertEqual(len(fake.calls), 3)
        self.assertEqual(fake.calls[1]["count"], 3)
        self.assertIn("from", fake.calls[1])
        self.assertNotIn("to", fake.calls[1])

    def test_count_over_cap_pages_backward(self) -> None:
        recent = [_candle(_hour(i)) for i in range(3, 6)]
        older = [_candle(_hour(i)) for i in range(0, 3)]
        fake = _FakeGet(
            [
                {"candles": recent},
                {"candles": older},
            ]
        )
        with patch.object(oanda_client, "get", fake):
            with patch.object(oanda_client, "MAX_CANDLES_PER_REQUEST", 3):
                payload = asyncio.run(
                    oanda_client.get_candles("GBP_USD", granularity="H1", count=5)
                )
        times = [c["time"] for c in payload["candles"]]
        self.assertEqual(times, [_hour(i) for i in range(1, 6)])
        self.assertEqual(fake.calls[0]["count"], 3)
        self.assertNotIn("to", fake.calls[0])
        self.assertIn("to", fake.calls[1])

    def test_from_count_over_cap_pages_forward(self) -> None:
        fake = _FakeGet(
            [
                {"candles": [_candle(_hour(i)) for i in range(0, 3)]},
                {"candles": [_candle(_hour(i)) for i in range(3, 5)]},
            ]
        )
        with patch.object(oanda_client, "get", fake):
            with patch.object(oanda_client, "MAX_CANDLES_PER_REQUEST", 3):
                payload = asyncio.run(
                    oanda_client.get_candles(
                        "GBP_USD",
                        granularity="H1",
                        count=5,
                        from_time=_hour(0),
                    )
                )
        times = [c["time"] for c in payload["candles"]]
        self.assertEqual(times, [_hour(i) for i in range(5)])

    def test_still_rejects_from_to_count(self) -> None:
        with self.assertRaises(oanda_client.OandaError):
            asyncio.run(
                oanda_client.get_candles(
                    "GBP_USD",
                    count=10,
                    from_time=_hour(0),
                    to_time=_hour(3),
                )
            )


if __name__ == "__main__":
    unittest.main()
