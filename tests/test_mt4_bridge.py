"""Unit tests for app.mt4_bridge (no Wine / no network)."""

from __future__ import annotations

import json
import tempfile
import threading
import time
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
        self._prev_ack = mt4_bridge.WAIT_FOR_ACK
        self._tmp = tempfile.TemporaryDirectory()
        settings.mt4_files_dir = self._tmp.name
        mt4_bridge.WAIT_FOR_ACK = False

    def tearDown(self) -> None:
        mt4_bridge.WAIT_FOR_ACK = self._prev_ack
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
        raw = cmd_path.read_text(encoding="utf-8")
        self.assertIn("\n", raw)
        self.assertTrue(all(len(line) < 4095 for line in raw.splitlines()))

    def test_write_command_pretty_prints_under_mql4_line_limit(self) -> None:
        # Compact one-line JSON >4095 chars is split mid-t1 by FILE_TXT FileReadString.
        objects = [
            {
                "name": f"sbox.regime.sma20.{i}",
                "type": "trend",
                "t1": 1_717_200_000 + i * 86400,
                "p1": 1.27 + i * 0.0001,
                "t2": 1_717_200_000 + (i + 1) * 86400,
                "p2": 1.27 + (i + 1) * 0.0001,
                "color": "cyan",
                "width": 1,
            }
            for i in range(200)
        ]
        compact = json.dumps(
            {"op": "upsert", "objects": objects}, separators=(",", ":")
        )
        self.assertGreater(len(compact), 4095)
        mt4_bridge.write_command({"op": "upsert", "objects": objects})
        raw = (mt4_bridge.inbox_dir() / mt4_bridge.CMD_NAME).read_text(
            encoding="utf-8"
        )
        self.assertIn("\n", raw)
        self.assertTrue(all(len(line) < 4095 for line in raw.splitlines()))
        self.assertIn('"t1": 1717200000', raw)

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

    def test_wait_for_cmd_ack_without_ea(self) -> None:
        self.assertTrue(mt4_bridge.wait_for_cmd_ack("x", timeout=0.2))

    def test_wait_for_cmd_ack_matches_heartbeat(self) -> None:
        self._write_heartbeat()
        mt4_bridge.WAIT_FOR_ACK = True
        cmd_id = "ack-test-id"

        def _ack() -> None:
            time.sleep(0.08)
            path = mt4_bridge.inbox_dir() / mt4_bridge.HEARTBEAT_NAME
            hb = json.loads(path.read_text(encoding="utf-8"))
            hb["last_cmd_id"] = cmd_id
            path.write_text(json.dumps(hb), encoding="utf-8")

        threading.Thread(target=_ack, daemon=True).start()
        self.assertTrue(mt4_bridge.wait_for_cmd_ack(cmd_id, timeout=2.0))

    def test_write_command_waits_for_ack_before_return(self) -> None:
        self._write_heartbeat()
        mt4_bridge.WAIT_FOR_ACK = True
        seen: list[str] = []

        def _ack() -> None:
            time.sleep(0.08)
            cmd = json.loads(
                (mt4_bridge.inbox_dir() / mt4_bridge.CMD_NAME).read_text(
                    encoding="utf-8"
                )
            )
            seen.append(cmd["id"])
            path = mt4_bridge.inbox_dir() / mt4_bridge.HEARTBEAT_NAME
            hb = json.loads(path.read_text(encoding="utf-8"))
            hb["last_cmd_id"] = cmd["id"]
            path.write_text(json.dumps(hb), encoding="utf-8")

        threading.Thread(target=_ack, daemon=True).start()
        cmd_id = mt4_bridge.write_command({"op": "clear", "prefix": "sbox."})
        self.assertEqual(seen, [cmd_id])
        self.assertEqual(
            json.loads(
                (mt4_bridge.inbox_dir() / mt4_bridge.HEARTBEAT_NAME).read_text(
                    encoding="utf-8"
                )
            )["last_cmd_id"],
            cmd_id,
        )


class TestRegimeObjects(unittest.TestCase):
    def test_regime_to_objects_has_bands_mas_and_label(self) -> None:
        from datetime import datetime, timedelta, timezone

        from app import regime
        from tests.test_indicators import _trend_bars

        raw = _trend_bars(80, step=0.01)
        start = datetime(2024, 6, 1, tzinfo=timezone.utc)
        bars = []
        for i, b in enumerate(raw):
            nb = dict(b)
            ts = start + timedelta(hours=i)
            nb["time"] = ts.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
            bars.append(nb)
        analysis = regime.analyze_bars(bars)
        objects = mt4_bridge.regime_to_objects(analysis, bars, offset_seconds=0)
        types = {o["type"] for o in objects}
        self.assertIn("trend", types)
        self.assertIn("hline", types)
        self.assertIn("label", types)
        names = [o["name"] for o in objects]
        self.assertTrue(any(n.startswith("sbox.regime.sma20.") for n in names))
        self.assertTrue(any("bb.upper_2" in n for n in names))
        self.assertTrue(any(n.endswith("high_n") or n.endswith("sbox.regime.high_n") or "high_n" in n for n in names))
        label = next(o for o in objects if o["name"].endswith("label") and "legend" not in o["name"])
        self.assertIn("regime=", label["text"])
        self.assertIn("ADX=", label["text"])
        legend = [o for o in objects if ".legend." in o["name"]]
        self.assertEqual(len(legend), len(mt4_bridge.REGIME_LEGEND))
        self.assertTrue(any("SMA 20" in o["text"] for o in legend))
        self.assertTrue(any("BB 2sd" in o["text"] for o in legend))
        self.assertLessEqual(len(objects), mt4_bridge.REGIME_MAX_OBJECTS + 20)
        for o in objects:
            if o["type"] == "trend":
                self.assertIsInstance(o["t1"], int)
                self.assertIsInstance(o["p1"], float)

    def test_regime_prefix_does_not_match_formation(self) -> None:
        self.assertNotEqual(mt4_bridge.REGIME_PREFIX, mt4_bridge.FORMATION_PREFIX)
        self.assertTrue(mt4_bridge.REGIME_PREFIX.startswith("sbox."))


class TestTicketObjects(unittest.TestCase):
    def test_ticket_to_objects_hlines_and_label(self) -> None:
        objs = mt4_bridge.ticket_to_objects(
            1.2700, 1.2680, 1.2740, side="long"
        )
        names = {o["name"]: o for o in objs}
        self.assertIn("sbox.ticket.entry", names)
        self.assertIn("sbox.ticket.stop", names)
        self.assertIn("sbox.ticket.target", names)
        self.assertIn("sbox.ticket.label", names)
        self.assertEqual(names["sbox.ticket.entry"]["type"], "hline")
        self.assertAlmostEqual(names["sbox.ticket.entry"]["p1"], 1.2700)
        self.assertAlmostEqual(names["sbox.ticket.stop"]["p1"], 1.2680)
        self.assertEqual(names["sbox.ticket.stop"]["color"], "red")
        self.assertEqual(names["sbox.ticket.target"]["color"], "green")
        self.assertIn("ticket long", names["sbox.ticket.label"]["text"])
        self.assertNotIn("sbox.ticket.time", names)

    def test_ticket_marks_timestamp(self) -> None:
        t1 = 1717200000
        objs = mt4_bridge.ticket_to_objects(
            1.2700,
            1.2680,
            1.2740,
            side="long",
            at_time=t1,
            time_label="2024-06-01 00:00 UTC",
        )
        names = {o["name"]: o for o in objs}
        self.assertEqual(names["sbox.ticket.time"]["type"], "vline")
        self.assertEqual(names["sbox.ticket.time"]["t1"], t1)
        self.assertEqual(names["sbox.ticket.time.arrow"]["type"], "arrow")
        self.assertEqual(names["sbox.ticket.time.arrow"]["arrow_code"], 233)
        self.assertEqual(names["sbox.ticket.time.text"]["type"], "text")
        self.assertIn("2024-06-01", names["sbox.ticket.time.text"]["text"])
        self.assertIn("@ 2024-06-01", names["sbox.ticket.label"]["text"])

    def test_ticket_prefix_distinct(self) -> None:
        self.assertNotEqual(mt4_bridge.TICKET_PREFIX, mt4_bridge.REGIME_PREFIX)
        self.assertNotEqual(mt4_bridge.TICKET_PREFIX, mt4_bridge.FORMATION_PREFIX)
        self.assertTrue(mt4_bridge.TICKET_PREFIX.startswith("sbox."))

    def test_ticket_rejects_incomplete(self) -> None:
        with self.assertRaises(mt4_bridge.Mt4BridgeError):
            mt4_bridge.ticket_to_objects(1.27, 1.27, 1.29)
        with self.assertRaises(mt4_bridge.Mt4BridgeError):
            mt4_bridge.ticket_to_objects(0, 1.26, 1.29)


class TestTicketInbox(unittest.TestCase):
    def setUp(self) -> None:
        self._prev = settings.mt4_files_dir
        self._prev_ack = mt4_bridge.WAIT_FOR_ACK
        self._tmp = tempfile.TemporaryDirectory()
        settings.mt4_files_dir = self._tmp.name
        mt4_bridge.WAIT_FOR_ACK = False

    def tearDown(self) -> None:
        mt4_bridge.WAIT_FOR_ACK = self._prev_ack
        settings.mt4_files_dir = self._prev
        self._tmp.cleanup()

    def _write_heartbeat(self, symbol: str = "GBPUSD", period: int = 1440) -> None:
        inbox = mt4_bridge.ensure_inbox()
        payload = {
            "symbol": symbol,
            "period": period,
            "timeframe": "D1",
            "time_current": 1_000_000,
            "time_gmt": 1_000_000,
            "offset_seconds": 0,
            "ea_ok": True,
            "last_cmd_id": "",
            "last_error": "",
            "object_count": 0,
            "last_prefix": "",
        }
        (inbox / mt4_bridge.HEARTBEAT_NAME).write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def test_apply_ticket_writes_cmd(self) -> None:
        self._write_heartbeat()
        result = mt4_bridge.apply_ticket(
            "GBP_USD", "D", 1.27, 1.26, 1.29, side="long"
        )
        self.assertTrue(result["ok"], result)
        cmd = json.loads(
            (mt4_bridge.inbox_dir() / mt4_bridge.CMD_NAME).read_text(encoding="utf-8")
        )
        self.assertEqual(cmd["op"], "upsert")
        self.assertEqual(cmd["prefix"], "sbox.ticket.")
        names = [o["name"] for o in cmd["objects"]]
        self.assertIn("sbox.ticket.entry", names)
        self.assertTrue(cmd["clear_prefix_first"])

    def test_apply_ticket_converts_rfc3339_to_broker_time(self) -> None:
        self._write_heartbeat()
        inbox = mt4_bridge.ensure_inbox()
        hb = json.loads((inbox / mt4_bridge.HEARTBEAT_NAME).read_text(encoding="utf-8"))
        hb["offset_seconds"] = 3600
        (inbox / mt4_bridge.HEARTBEAT_NAME).write_text(json.dumps(hb), encoding="utf-8")
        result = mt4_bridge.apply_ticket(
            "GBP_USD",
            "D",
            1.27,
            1.26,
            1.29,
            side="long",
            at_time="2024-06-01T00:00:00Z",
        )
        self.assertTrue(result["ok"], result)
        cmd = json.loads(
            (mt4_bridge.inbox_dir() / mt4_bridge.CMD_NAME).read_text(encoding="utf-8")
        )
        vline = next(o for o in cmd["objects"] if o["type"] == "vline")
        self.assertEqual(vline["t1"], 1717200000 + 3600)
        self.assertIn("sbox.ticket.time", vline["name"])


if __name__ == "__main__":
    unittest.main()
