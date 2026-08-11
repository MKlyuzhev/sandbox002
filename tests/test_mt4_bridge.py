"""Unit tests for app.mt4_bridge (no Wine / no network)."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from app import mt4_bridge, patterns
from app.config import settings
from tests.test_patterns import TestHsTop


def _rfc_bars(bars: list[dict]) -> list[dict]:
    """Rewrite synthetic bars with hourly RFC3339 times from a UTC start."""
    start = datetime(2024, 6, 1, tzinfo=timezone.utc)
    out: list[dict] = []
    for i, b in enumerate(bars):
        nb = dict(b)
        ts = start + timedelta(hours=i)
        nb["time"] = ts.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
        out.append(nb)
    return out


class TestMapsAndTime(unittest.TestCase):
    def test_map_symbol(self) -> None:
        self.assertEqual(mt4_bridge.map_symbol("GBP_USD"), "GBPUSD")
        self.assertEqual(mt4_bridge.map_symbol("eur/usd"), "EURUSD")
        self.assertEqual(mt4_bridge.map_symbol("USDJPY"), "USDJPY")

    def test_map_timeframe(self) -> None:
        self.assertEqual(mt4_bridge.map_timeframe("H1"), ("H1", 60))
        self.assertEqual(mt4_bridge.map_timeframe("D"), ("D1", 1440))
        with self.assertRaises(mt4_bridge.Mt4BridgeError):
            mt4_bridge.map_timeframe("S5")

    def test_parse_rfc3339_and_offset(self) -> None:
        utc = mt4_bridge.parse_rfc3339_utc("2024-06-01T00:00:00.000000000Z")
        self.assertEqual(utc, 1717200000)
        self.assertEqual(mt4_bridge.broker_time(utc, 7200), 1717200000 + 7200)
        hb = {"time_current": 1007200, "time_gmt": 1000000}
        self.assertEqual(mt4_bridge.offset_from_heartbeat(hb), 7200)
        self.assertEqual(
            mt4_bridge.offset_from_heartbeat({"offset_seconds": 3600}), 3600
        )


class TestFormationObjects(unittest.TestCase):
    def test_hs_break_emits_layers(self) -> None:
        raw = TestHsTop()._hs_top_bars(break_neckline=True)
        bars = _rfc_bars(raw)
        analysis = patterns.analyze_bars(bars, swing_left=2, swing_right=2)
        objs = mt4_bridge.formation_to_objects(analysis, bars, offset_seconds=3600)
        types = {o["type"] for o in objs}
        names = {o["name"] for o in objs}
        self.assertIn("arrow", types)
        self.assertIn("trend", types)
        self.assertIn("label", types)
        self.assertTrue(any(n.startswith("sbox.formation.swing.") for n in names))
        self.assertIn("sbox.formation.stage", names)
        if analysis.get("hs", {}).get("stage") == "confirmed_break":
            self.assertIn("sbox.formation.min_target", names)
            self.assertTrue(any(o["type"] == "hline" for o in objs))
        sample = next(o for o in objs if o["type"] == "arrow")
        idx = next(
            s["index"]
            for s in analysis["swings"]
            if abs(s["price"] - sample["p1"]) < 1e-9
        )
        expected = mt4_bridge.parse_rfc3339_utc(bars[idx]["time"]) + 3600
        self.assertEqual(sample["t1"], expected)

    def test_skips_unparseable_times(self) -> None:
        bars = TestHsTop()._hs_top_bars(break_neckline=False)
        analysis = patterns.analyze_bars(bars, swing_left=2, swing_right=2)
        objs = mt4_bridge.formation_to_objects(analysis, bars, offset_seconds=0)
        # only the stage label (no t1) should remain
        self.assertTrue(all(o["type"] == "label" for o in objs))


class TestInbox(unittest.TestCase):
    def setUp(self) -> None:
        self._prev = settings.mt4_files_dir
        self._tmp = tempfile.TemporaryDirectory()
        settings.mt4_files_dir = self._tmp.name

    def tearDown(self) -> None:
        settings.mt4_files_dir = self._prev
        self._tmp.cleanup()

    def _write_heartbeat(
        self, symbol: str = "GBPUSD", period: int = 60, ea_ok: bool = True
    ) -> None:
        inbox = mt4_bridge.ensure_inbox()
        payload = {
            "symbol": symbol,
            "period": period,
            "timeframe": "H1",
            "time_current": 1_000_000,
            "time_gmt": 1_000_000,
            "offset_seconds": 0,
            "ea_ok": ea_ok,
            "last_cmd_id": "",
            "last_error": "",
            "object_count": 0,
            "last_prefix": "",
        }
        (inbox / mt4_bridge.HEARTBEAT_NAME).write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def test_status_writable_without_ea(self) -> None:
        st = mt4_bridge.status()
        self.assertTrue(st["inbox_writable"])
        self.assertFalse(st["ea_ok"])
        self.assertIsNone(st["heartbeat"])

    def test_upsert_writes_cmd_when_chart_matches(self) -> None:
        self._write_heartbeat("GBPUSD", 60)
        result = mt4_bridge.upsert_objects(
            "GBP_USD",
            "H1",
            [
                {
                    "name": "line1",
                    "type": "trend",
                    "t1": 1717200000,
                    "p1": 1.27,
                    "t2": 1717286400,
                    "p2": 1.268,
                    "color": "cyan",
                    "ray": True,
                }
            ],
            prefix="sbox.formation.",
        )
        self.assertTrue(result["ok"], result)
        cmd_path = mt4_bridge.inbox_dir() / mt4_bridge.CMD_NAME
        cmd = json.loads(cmd_path.read_text(encoding="utf-8"))
        self.assertEqual(cmd["op"], "upsert")
        self.assertEqual(cmd["symbol"], "GBPUSD")
        self.assertEqual(cmd["objects"][0]["name"], "sbox.formation.line1")
        self.assertTrue(cmd["clear_prefix_first"])

    def test_upsert_refuses_symbol_mismatch(self) -> None:
        self._write_heartbeat("EURUSD", 60)
        result = mt4_bridge.upsert_objects(
            "GBP_USD",
            "H1",
            [{"name": "x", "type": "hline", "p1": 1.1}],
        )
        self.assertFalse(result["ok"])
        self.assertFalse(result["chart_ok"])
        self.assertIn("symbol", result["error"].lower())
        self.assertFalse((mt4_bridge.inbox_dir() / mt4_bridge.CMD_NAME).exists())

    def test_clear_layer_writes_cmd(self) -> None:
        self._write_heartbeat()
        result = mt4_bridge.clear_layer("sbox.")
        self.assertTrue(result["ok"], result)
        cmd = json.loads(
            (mt4_bridge.inbox_dir() / mt4_bridge.CMD_NAME).read_text(encoding="utf-8")
        )
        self.assertEqual(cmd["op"], "clear")
        self.assertEqual(cmd["prefix"], "sbox.")


if __name__ == "__main__":
    unittest.main()
