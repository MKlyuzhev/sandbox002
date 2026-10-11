"""Unit tests for app.mtf_channel (no Wine / no network)."""

from __future__ import annotations

import asyncio
import json
import math
import tempfile
import unittest
from pathlib import Path

from app import mt4_bridge, mtf_channel, oanda_mcp
from app.config import settings

FIXTURE = Path(__file__).parent / "fixtures" / "gbpusd_w1_grid.json"
START = 946771200  # 2000-01-02, a Sunday


def _bars_from_rows(rows: list[list[float]]) -> list[dict]:
    return [
        {"time": int(t), "open": o, "high": h, "low": low, "close": c, "complete": True}
        for t, o, h, low, c in rows
    ]


def _channel_bars(n: int = 900, slope: float = 0.05, width: float = 20.0, up: bool = True) -> list[dict]:
    """Price swinging rail to rail inside a straight channel (period 148 weeks)."""
    bars = []
    for i in range(n):
        base = 100 + slope * i if up else 200 - slope * i
        close = base + width * (0.5 + 0.5 * math.sin(2 * math.pi * i / 148))
        bars.append(
            {"time": START + i * mtf_channel.WEEK, "open": close, "high": close + 0.2,
             "low": close - 0.2, "close": close, "complete": True}
        )
    return bars


class TestGbpUsdRegression(unittest.TestCase):
    """The trader's GBP/USD W1 grid reproduces docs/MTF_CHANNEL_ANALYSIS.md section 5."""

    @classmethod
    def setUpClass(cls) -> None:
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.bars = _bars_from_rows(data["bars"])
        cls.grid = mtf_channel.grid_from_chart(cls.bars, data["objects"])
        cls.read = mtf_channel.read_grid(cls.bars, cls.grid, instrument="GBP_USD")

    def test_roles_from_style_and_position(self) -> None:
        self.assertEqual(self.grid.names["air_upper"], "Trendline 61174")
        self.assertEqual(self.grid.names["center"], "Trendline 60257")
        self.assertEqual(self.grid.names["air_lower"], "Trendline 28437")
        self.assertEqual(self.grid.warnings, [])

    def test_trend_and_lines(self) -> None:
        r = self.read
        self.assertEqual(r["trend"]["direction"], "down")
        self.assertAlmostEqual(r["trend"]["pips_per_year"], -272, delta=1)
        self.assertEqual(r["trend"]["since"], "2004-04-18")
        expected = {"upper": 1.3598, "air_upper": 1.2756, "center": 1.1420,
                    "air_lower": 1.0104, "lower": 0.9291}
        for role, value in expected.items():
            self.assertAlmostEqual(r["lines_now"][role], value, delta=0.00006, msg=role)

    def test_zones(self) -> None:
        r = self.read
        self.assertEqual(r["weeks"], 1172)
        self.assertEqual(
            r["zone_shares_pct"],
            {"below": 0.2, "lower_air": 5.0, "lower_body": 25.1,
             "upper_body": 46.2, "upper_air": 15.0, "above": 8.5},
        )
        self.assertEqual(r["zone"]["now"], "upper_air")
        self.assertEqual(r["zone"]["closes"], 76)
        self.assertEqual(r["zone"]["from_date"][:7], "2025-04")

    def test_air_visits(self) -> None:
        v = self.read["visits"]
        self.assertEqual(len(v["lower_air"]), 6)
        self.assertTrue(all(x["left_to"] == "lower_body" for x in v["lower_air"]))
        self.assertEqual(len(v["upper_air"]), 8)
        self.assertEqual(self.read["past_outcomes_of_current_zone"], {"body": 5, "above": 2})
        self.assertEqual([x["closes"] for x in v["above"]][:2], [92, 7])
        self.assertEqual(len(v["below"]), 2)


class TestProposal(unittest.TestCase):
    def test_up_channel(self) -> None:
        bars = _channel_bars()
        p = mtf_channel.propose_channel(bars)
        self.assertEqual(p["direction"], "up")
        self.assertAlmostEqual(p["slope_per_week"], 0.05, places=6)
        self.assertGreaterEqual(p["rail"]["touches"], 3)
        best = p["candidates"][0]
        self.assertAlmostEqual(best["width"], 20.4, delta=0.05)
        self.assertGreaterEqual(len(best["touches"]), 3)
        self.assertEqual(best["closes_beyond_pct"], 0.0)

    def test_down_channel(self) -> None:
        bars = _channel_bars(up=False)
        p = mtf_channel.propose_channel(bars)
        self.assertEqual(p["direction"], "down")
        self.assertEqual(p["rail"]["side"], "upper")
        self.assertAlmostEqual(p["slope_per_week"], -0.05, places=6)
        self.assertAlmostEqual(p["candidates"][0]["width"], 20.4, delta=0.05)

    def test_objects_round_trip_through_chart(self) -> None:
        bars = _channel_bars()
        p = mtf_channel.propose_channel(bars)
        grid = mtf_channel.grid_from_proposal(bars, p)
        objs = mtf_channel.grid_objects(grid, "USD_JPY")
        self.assertTrue(all(o["editable"] for o in objs))
        self.assertEqual(objs[0]["name"], "Channel USDJPY W1")
        back = mtf_channel.grid_from_chart(bars, objs)
        self.assertEqual(back.warnings, [])
        last = len(bars) - 1
        for role in mtf_channel.ROLES:
            self.assertAlmostEqual(back.lines[role].at(last), grid.lines[role].at(last), places=6)

    def test_too_few_bars(self) -> None:
        with self.assertRaises(mtf_channel.ChannelError):
            mtf_channel.propose_channel(_channel_bars(n=100))


class TestBars(unittest.TestCase):
    def test_merge_oanda_weeks_stamps_sunday(self) -> None:
        # OANDA week opening Fri 2026-10-02 21:00 UTC = Sat 00:00 server; MT4 stamps Sunday.
        bars = [{"time": 1790467200, "open": 1, "high": 1, "low": 1, "close": 1, "complete": True}]
        candle = {"time": "2026-10-02T21:00:00.000000000Z", "complete": True,
                  "bid": {"o": "157.670", "h": "158.503", "l": "157.431", "c": "158.259"}}
        merged, added = mtf_channel.merge_oanda_weeks(bars, [candle])
        self.assertEqual(added, 1)
        self.assertEqual(merged[-1]["time"], 1790467200 + mtf_channel.WEEK)
        self.assertEqual(merged[-1]["close"], 158.259)

    def test_read_skips_incomplete_week(self) -> None:
        bars = _channel_bars()
        grid = mtf_channel.grid_from_proposal(bars, mtf_channel.propose_channel(bars))
        live = dict(bars[-1], time=bars[-1]["time"] + mtf_channel.WEEK, close=999.0, complete=False)
        r = mtf_channel.read_grid(bars + [live], grid)
        self.assertEqual(r["last_close"]["close"], bars[-1]["close"])


class TestMcpTools(unittest.TestCase):
    def setUp(self) -> None:
        self._prev = settings.mt4_files_dir
        self._tmp = tempfile.TemporaryDirectory()
        settings.mt4_files_dir = self._tmp.name

    def tearDown(self) -> None:
        settings.mt4_files_dir = self._prev
        self._tmp.cleanup()

    def test_draw_refuses_without_live_w1_chart(self) -> None:
        out = asyncio.run(oanda_mcp.mt4_draw_mtf_grid("USD_JPY"))
        self.assertFalse(out["ok"])
        self.assertIn("SandboxChartBridge", out["error"])

    def test_read_rejects_unknown_source(self) -> None:
        self.assertEqual(mt4_bridge.map_symbol("USD_JPY"), "USDJPY")
        out = asyncio.run(oanda_mcp.mtf_channel_read("USD_JPY", source="nope"))
        self.assertFalse(out["ok"])


if __name__ == "__main__":
    unittest.main()
