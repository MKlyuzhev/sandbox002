"""Unit tests for app.mtf_channel (no Wine / no network)."""

from __future__ import annotations

import asyncio
import json
import math
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from app import mt4_bridge, mtf_channel, oanda_mcp
from app.config import settings

FIXTURE = Path(__file__).parent / "fixtures" / "gbpusd_w1_grid.json"
START = 946771200  # 2000-01-02, a Sunday
D1_START = 946857600  # 2000-01-03, a Monday


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


def _d1_channel_bars(n: int = 400, slope: float = 0.05, width: float = 5.0, up: bool = True) -> list[dict]:
    """Weekday-only daily bars swinging rail to rail (period 60 bars)."""
    bars = []
    t = D1_START
    for i in range(n):
        base = 100 + slope * i if up else 200 - slope * i
        close = base + width * (0.5 + 0.5 * math.sin(2 * math.pi * i / 60))
        bars.append({"time": t, "open": close, "high": close + 0.05, "low": close - 0.05,
                     "close": close, "complete": True})
        t += mtf_channel.DAY
        while datetime.fromtimestamp(t, timezone.utc).weekday() >= 5:
            t += mtf_channel.DAY
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


class TestD1(unittest.TestCase):
    def test_up_channel(self) -> None:
        p = mtf_channel.propose_channel(_d1_channel_bars(), "D1")
        self.assertEqual(p["timeframe"], "D1")
        self.assertEqual(p["direction"], "up")
        self.assertAlmostEqual(p["slope_per_day"], 0.05, places=6)
        self.assertAlmostEqual(p["candidates"][0]["width"], 5.1, delta=0.05)

    def test_down_channel(self) -> None:
        p = mtf_channel.propose_channel(_d1_channel_bars(up=False), "D1")
        self.assertEqual(p["direction"], "down")
        self.assertEqual(p["rail"]["side"], "upper")
        self.assertAlmostEqual(p["candidates"][0]["width"], 5.1, delta=0.05)

    def test_origin_is_within_one_year(self) -> None:
        bars = _d1_channel_bars(n=800)
        p = mtf_channel.propose_channel(bars, "D1")
        self.assertGreaterEqual(p["origin"]["time"], bars[-260]["time"])

    def test_too_few_bars(self) -> None:
        with self.assertRaises(mtf_channel.ChannelError):
            mtf_channel.propose_channel(_d1_channel_bars(n=15), "D1")

    def test_unknown_timeframe(self) -> None:
        with self.assertRaises(mtf_channel.ChannelError):
            mtf_channel.get_tf("H7")

    def test_read_keys_and_live_day(self) -> None:
        bars = _d1_channel_bars()
        grid = mtf_channel.grid_from_proposal(bars, mtf_channel.propose_channel(bars, "D1"), 0, "D1")
        live = dict(bars[-1], time=bars[-1]["time"] + mtf_channel.DAY, close=999.0, complete=False)
        r = mtf_channel.read_grid(bars + [live], grid)
        self.assertEqual(r["timeframe"], "D1")
        self.assertEqual(r["last_close"]["close"], bars[-1]["close"])
        self.assertIn("lines_next_day", r)
        self.assertIn("days", r)
        self.assertIn("pips_per_day", r["trend"])
        self.assertNotIn("weeks", r)

    def test_objects_round_trip_through_chart(self) -> None:
        bars = _d1_channel_bars()
        grid = mtf_channel.grid_from_proposal(bars, mtf_channel.propose_channel(bars, "D1"), 0, "D1")
        objs = mtf_channel.grid_objects(grid, "USD_JPY")
        self.assertEqual(objs[0]["name"], "Channel USDJPY D1")
        self.assertEqual(objs[2]["name"], "Trendline USDJPY D1 center")
        back = mtf_channel.grid_from_chart(bars, objs, tf="D1")
        self.assertEqual(back.warnings, [])
        last = len(bars) - 1
        for role in mtf_channel.ROLES:
            self.assertAlmostEqual(back.lines[role].at(last), grid.lines[role].at(last), places=6)

    def test_picks_own_channel_when_w1_grid_is_also_on_the_chart(self) -> None:
        bars = _d1_channel_bars()
        grid = mtf_channel.grid_from_proposal(bars, mtf_channel.propose_channel(bars, "D1"), 0, "D1")
        d1 = mtf_channel.grid_objects(grid, "USD_JPY")
        w1 = [dict(o, name=o["name"].replace(" D1", " W1"), p1=o["p1"] + 30, p2=o["p2"] + 30,
                   **({"p3": o["p3"] + 30} if "p3" in o else {})) for o in d1]
        back = mtf_channel.grid_from_chart(bars, w1 + d1, tf="D1")
        self.assertEqual(back.names["upper"], "Channel USDJPY D1")
        self.assertEqual(back.names["center"], "Trendline USDJPY D1 center")

    def test_bar_index_skips_weekends_past_the_end(self) -> None:
        bars = _d1_channel_bars(n=5)  # Mon 2000-01-03 .. Fri 2000-01-07
        index = mtf_channel.BarIndex(bars, mtf_channel.DAY)
        monday = bars[-1]["time"] + 3 * mtf_channel.DAY
        self.assertEqual(index.index(monday), 5)
        self.assertEqual(index.time(5), monday)

    def test_merge_oanda_days(self) -> None:
        # OANDA day opening Thu 2026-10-08 21:00 UTC (17:00 NY) = Fri 00:00 broker, no shift.
        bars = [{"time": 1791417600, "open": 1, "high": 1, "low": 1, "close": 1, "complete": True}]
        fri = {"time": "2026-10-08T21:00:00.000000000Z", "complete": True,
               "bid": {"o": "153.0", "h": "153.5", "l": "152.8", "c": "153.2"}}
        sat = {"time": "2026-10-09T21:00:00.000000000Z", "complete": True,
               "bid": {"o": "153.2", "h": "153.2", "l": "153.2", "c": "153.2"}}
        merged, added = mtf_channel.merge_oanda_bars(bars, [fri, sat], "D1")
        self.assertEqual(added, 1)
        self.assertEqual(merged[-1]["time"], 1791417600 + mtf_channel.DAY)
        self.assertEqual(datetime.fromtimestamp(merged[-1]["time"], timezone.utc).weekday(), 4)


def _zigzag_bars(points: list[tuple[int, float]]) -> list[dict]:
    """Weekday daily bars moving in straight lines between (bar, price) points."""
    closes: list[float] = []
    for (i0, p0), (i1, p1) in zip(points, points[1:]):
        closes += [p0 + (p1 - p0) * k / (i1 - i0) for k in range(i1 - i0)]
    closes.append(points[-1][1])
    bars = _d1_channel_bars(n=len(closes))
    for b, c in zip(bars, closes):
        b.update(open=c, high=c + 0.05, low=c - 0.05, close=c)
    return bars


def _up_then_down(
    secs: int = mtf_channel.DAY, n_up: int = 300, n_down: int = 200, width: float = 3.0
) -> list[dict]:
    """An up channel (slope 0.05/bar) into its top, then a channel down (0.04/bar)."""
    up = _d1_channel_bars(n=n_up, slope=0.05, width=width)
    top = max(range(n_up), key=lambda i: up[i]["high"])
    down = _d1_channel_bars(n=n_down, slope=0.04, width=width, up=False)
    shift = up[top]["high"] - max(b["high"] for b in down[:30])
    bars = up[: top + 1] + [
        dict(b, open=b["open"] + shift, high=b["high"] + shift, low=b["low"] + shift,
             close=b["close"] + shift) for b in down
    ]
    index = mtf_channel.BarIndex(bars[:1], secs)
    for i, b in enumerate(bars):
        b["time"] = index.time(i)
    return bars


class TestLegs(unittest.TestCase):
    def test_legs_cut_on_reversals(self) -> None:
        bars = _zigzag_bars([(0, 100), (40, 120), (50, 117), (80, 105), (120, 125), (140, 112)])
        lg = mtf_channel.legs(bars, threshold=10)
        # the 120 -> 117 dip is smaller than 10: no leg; 125 -> 112 is the leg in progress
        self.assertEqual([x["direction"] for x in lg], ["up", "down", "up", "down"])
        self.assertEqual([x["start_index"] for x in lg], [0, 40, 80, 120])
        self.assertAlmostEqual(lg[1]["start_price"], 120.05)
        self.assertAlmostEqual(lg[2]["start_price"], 104.95)
        self.assertIsNone(lg[-1]["end_index"])
        self.assertAlmostEqual(lg[-1]["extreme_so_far"]["price"], 111.95)

    def test_leg_proposals_use_each_legs_bars(self) -> None:
        w1 = _channel_bars()
        w1_grid = mtf_channel.grid_from_proposal(w1, mtf_channel.propose_channel(w1))
        width = mtf_channel.w1_width(w1_grid)
        self.assertAlmostEqual(width, 20.4, delta=0.05)  # leg reversal = 4.08
        bars = _up_then_down()
        r = mtf_channel.leg_proposals(bars, w1, w1_grid)
        self.assertEqual(r["previous"]["direction"], "up")
        self.assertEqual(r["current"]["direction"], "down")
        self.assertEqual(r["current"]["leg"]["start"]["date"], r["current"]["origin"]["date"])
        grid = mtf_channel.grid_from_leg(bars, r, "previous")
        objs = mtf_channel.grid_objects(grid, "USD_JPY")
        self.assertEqual(objs[0]["name"], f"Channel USDJPY D1 {r['previous']['leg']['label']}")
        self.assertEqual(objs[0]["color"], mtf_channel.PREVIOUS_LEG_COLOR)
        self.assertEqual(mtf_channel.leg_channel_name("USD_JPY", r, "previous"), objs[0]["name"])

    def test_leg_must_be_known(self) -> None:
        with self.assertRaises(mtf_channel.ChannelError):
            mtf_channel.grid_from_leg([], {}, "next")


class TestH1(unittest.TestCase):
    def setUp(self) -> None:
        self._prev = settings.mt4_files_dir
        self._tmp = tempfile.TemporaryDirectory()
        settings.mt4_files_dir = self._tmp.name

    def tearDown(self) -> None:
        settings.mt4_files_dir = self._prev
        self._tmp.cleanup()

    @staticmethod
    async def _fake_load(_instrument, tf, **_kw):
        tf = mtf_channel.get_tf(tf)
        if tf.name == "W1":
            return {"bars": _channel_bars()}
        return {"bars": _up_then_down(width=6.0)}  # D1: legs of >= 4.08 inside W1 width 20.4

    def test_bar_index_skips_weekend_hours(self) -> None:
        fri_23 = D1_START + 4 * mtf_channel.DAY + 23 * 3600  # Fri 2000-01-07 23:00
        bars = [{"time": fri_23 - 3600}, {"time": fri_23}]
        index = mtf_channel.BarIndex(bars, 3600)
        monday = fri_23 + 3600 + 2 * mtf_channel.DAY
        self.assertEqual(index.time(2), monday)
        self.assertEqual(index.index(monday), 2)
        self.assertEqual(index.index(monday + 3600), 3)

    def test_merge_oanda_hours(self) -> None:
        bars = [{"time": 1791583200, "open": 1, "high": 1, "low": 1, "close": 1, "complete": True}]
        fri = {"time": "2026-10-09T20:00:00.000000000Z", "complete": True,  # Fri 23:00 broker
               "bid": {"o": "158.2", "h": "158.3", "l": "158.1", "c": "158.25"}}
        sat = {"time": "2026-10-10T12:00:00.000000000Z", "complete": True,  # Sat 15:00 broker
               "bid": {"o": "158.2", "h": "158.2", "l": "158.2", "c": "158.2"}}
        merged, added = mtf_channel.merge_oanda_bars(bars, [fri, sat], "H1")
        self.assertEqual(added, 1)
        self.assertEqual(merged[-1]["time"], 1791583200 + 3600)

    def test_legs_inside_a_d1_grid(self) -> None:
        d1 = _d1_channel_bars()
        d1_grid = mtf_channel.grid_from_proposal(d1, mtf_channel.propose_channel(d1, "D1"), 0, "D1")
        h1 = _up_then_down(secs=3600, width=0.5)  # parent width 5.1 -> legs cut at 1.02
        r = mtf_channel.leg_proposals(h1, d1, d1_grid, "H1")
        self.assertAlmostEqual(r["leg_reversal"], 0.2 * mtf_channel.grid_width(d1_grid), places=6)
        self.assertIn("d1_grid", r)
        self.assertIn("d1_position", r["current"]["leg"]["start"])
        self.assertEqual(r["current"]["direction"], "down")
        grid = mtf_channel.grid_from_leg(h1, r, "current")
        objs = mtf_channel.grid_objects(grid, "USD_JPY")
        self.assertRegex(objs[0]["name"], r"^Channel USDJPY H1 leg \d{4}-\d\d-\d\d \d\d:\d\d$")
        self.assertEqual(objs[0]["color"], mtf_channel.H1.color)
        self.assertLessEqual(max(len(o["name"]) for o in objs), 63)

    def test_parent_grid_is_the_current_d1_leg(self) -> None:
        with mock.patch.object(mtf_channel, "load_bars", self._fake_load):
            bars, grid = asyncio.run(mtf_channel.parent_grid("USD_JPY", "H1"))
        self.assertEqual(grid.tf.name, "D1")
        self.assertTrue(grid.source.startswith("proposal: current leg"))
        with mock.patch.object(mtf_channel, "load_bars", self._fake_load):
            d1 = asyncio.run(mtf_channel.leg_proposals_for("USD_JPY", "D1", bars))
        self.assertEqual(grid.label, d1["current"]["leg"]["label"])

    def test_contexts_chain(self) -> None:
        with mock.patch.object(mtf_channel, "load_bars", self._fake_load):
            ctx = asyncio.run(mtf_channel.contexts("USD_JPY", "H1"))
        self.assertEqual(set(ctx), {"d1_context", "w1_context"})
        self.assertIn("lines_next_day", ctx["d1_context"])
        self.assertTrue(ctx["d1_context"]["leg"].startswith("leg "))
        self.assertIn("lines_next_week", ctx["w1_context"])

    def test_draw_refuses_without_live_h1_chart(self) -> None:
        out = asyncio.run(oanda_mcp.mt4_draw_mtf_grid("USD_JPY", timeframe="H1"))
        self.assertFalse(out["ok"])
        self.assertIn("SandboxChartBridge", out["error"])


class TestW1Context(unittest.TestCase):
    def setUp(self) -> None:
        self._prev = settings.mt4_files_dir
        self._tmp = tempfile.TemporaryDirectory()
        settings.mt4_files_dir = self._tmp.name

    def tearDown(self) -> None:
        settings.mt4_files_dir = self._prev
        self._tmp.cleanup()

    def test_falls_back_to_proposal_without_w1_grid(self) -> None:
        async def fake_load(_instrument, _tf, **_kw):
            return {"bars": _channel_bars()}

        with mock.patch.object(mtf_channel, "load_bars", fake_load):
            ctx = asyncio.run(mtf_channel.w1_context("USD_JPY"))
        self.assertTrue(ctx["source"].startswith("proposal candidate 0"))
        self.assertIn("lines_next_week", ctx)
        self.assertIn(ctx["zone"]["now"], mtf_channel.ZONES)

    def test_error_is_returned_not_raised(self) -> None:
        ctx = asyncio.run(mtf_channel.w1_context("USD_JPY", fetch=False))
        self.assertIn("error", ctx)


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

    def test_draw_refuses_without_live_d1_chart(self) -> None:
        out = asyncio.run(oanda_mcp.mt4_draw_mtf_grid("USD_JPY", timeframe="D1"))
        self.assertFalse(out["ok"])
        self.assertIn("SandboxChartBridge", out["error"])

    def test_tools_reject_unknown_timeframe(self) -> None:
        for out in (
            asyncio.run(oanda_mcp.mt4_draw_mtf_grid("USD_JPY", timeframe="H7")),
            asyncio.run(oanda_mcp.mtf_channel_propose("USD_JPY", timeframe="H7")),
            asyncio.run(oanda_mcp.mtf_channel_read("USD_JPY", timeframe="H7")),
        ):
            self.assertFalse(out["ok"])
            self.assertIn("timeframe", out["error"])

    def test_read_rejects_unknown_source(self) -> None:
        self.assertEqual(mt4_bridge.map_symbol("USD_JPY"), "USDJPY")
        out = asyncio.run(oanda_mcp.mtf_channel_read("USD_JPY", source="nope"))
        self.assertFalse(out["ok"])


if __name__ == "__main__":
    unittest.main()
