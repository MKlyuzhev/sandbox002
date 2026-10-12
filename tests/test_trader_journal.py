"""Trader journal wiring: SL/TP revisions, manual links, analyses, replay."""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agent.trader_ingest import build_corpus_text
from agent.trader_sync import review_pending, sync_chart_dir
from app import mt4_bridge, trader_journal
from app.config import settings
from app.trader_store import TraderStore

OFFSET = 10800  # broker = UTC+3


def _order(ticket: int = 7, **kw) -> dict:
    base = {
        "ticket": ticket,
        "symbol": "EURUSD",
        "type": "sell",
        "lots": 1.0,
        "open_price": 1.13863,
        "open_time": 1_790_581_976,
        "sl": 1.14014,
        "tp": 1.13235,
        "comment": "",
    }
    base.update(kw)
    return base


CHANNEL = {
    "name": "Channel 61631",
    "type": "channel",
    "t1": 1_788_962_400,
    "t2": 1_789_653_600,
    "t3": 1_789_110_000,
    "p1": 1.16576,
    "p2": 1.15314,
    "p3": 1.15597,
    "ray": True,
    "color": 255,
    "style": 0,
    "width": 1,
    "text": "",
    "tooltip": "",
}


class _Outbox:
    """Temporary MQL4/Files tree with one EURUSD_H1 chart folder."""

    def __init__(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.chart = self.root / "sandbox002" / "EURUSD_H1"
        self.chart.mkdir(parents=True)
        self.write(
            "heartbeat.json",
            {"symbol": "EURUSD", "period": 60, "timeframe": "H1", "offset_seconds": OFFSET,
             "time_current": 1_790_582_000, "time_gmt": 1_790_582_000 - OFFSET},
        )
        self.write("orders.json", {"orders": []})
        self.write("history.json", {"orders": []})
        self.chart_objects([])
        self.store = TraderStore(self.root / "t.sqlite")

    def write(self, name: str, payload) -> None:
        (self.chart / name).write_text(json.dumps(payload), encoding="utf-8")

    def chart_objects(self, objects: list[dict], ts: int = 1_790_582_000) -> None:
        self.write(
            "chart.json",
            {"symbol": "EURUSD", "timeframe": "H1", "time_current": ts, "objects": objects},
        )

    def events(self, rows: list[dict]) -> None:
        (self.chart / "events.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
        )

    def close(self) -> None:
        self.tmp.cleanup()


class TestRevisions(unittest.TestCase):
    def setUp(self) -> None:
        self.box = _Outbox()

    def tearDown(self) -> None:
        self.box.close()

    def test_events_become_revisions_idempotently(self) -> None:
        self.box.write("orders.json", {"orders": [_order(sl=1.142, tp=1.136)]})
        self.box.events(
            [
                {"ts": 1_790_581_976, "kind": "order_open", "ticket": 7, "sl": 1.14014, "tp": 1.13235, "price": 1.13863, "seq": 1},
                {"ts": 1_790_582_990, "kind": "order_modify", "ticket": 7, "sl": 1.142, "tp": 1.136, "price": 0, "seq": 2},
            ]
        )
        for _ in range(3):  # events.jsonl is re-read on every sync
            sync_chart_dir(self.box.store, self.box.chart, live=False)
        revs = self.box.store.revisions_for(7)
        self.assertEqual([(r["sl"], r["tp"]) for r in revs], [(1.14014, 1.13235), (1.142, 1.136)])
        order = self.box.store.get_order(7)
        self.assertEqual((order["sl_open"], order["tp_open"]), (1.14014, 1.13235))
        self.assertEqual(order["utc_offset"], OFFSET)

    def test_poll_only_from_live_folder(self) -> None:
        self.box.write("orders.json", {"orders": [_order()]})
        sync_chart_dir(self.box.store, self.box.chart, live=False)
        self.assertEqual(self.box.store.revisions_for(7), [])
        sync_chart_dir(self.box.store, self.box.chart, live=True)
        self.box.write("orders.json", {"orders": [_order(sl=1.142)]})
        self.box.chart_objects([], ts=1_790_583_000)
        sync_chart_dir(self.box.store, self.box.chart, live=True)
        sync_chart_dir(self.box.store, self.box.chart, live=True)
        self.assertEqual([r["sl"] for r in self.box.store.revisions_for(7)], [1.14014, 1.142])


class TestDeletedPending(unittest.TestCase):
    """Deleted pending orders never reach history.json; they must not stay open."""

    def setUp(self) -> None:
        self.box = _Outbox()
        self.pending = _order(8, type="sell_limit", open_price=1.145)
        self.box.write("orders.json", {"orders": [self.pending]})
        sync_chart_dir(self.box.store, self.box.chart, live=True)

    def tearDown(self) -> None:
        self.box.close()

    def status(self, ticket: int = 8) -> str:
        return self.box.store.get_order(ticket)["status"]

    def test_close_event_marks_deleted_and_stale_folder_cannot_reopen(self) -> None:
        self.box.write("orders.json", {"orders": []})
        self.box.events([{"ts": 1_790_583_000, "kind": "order_close", "ticket": 8, "sl": 1.15, "tp": 1.13, "price": 1.14, "seq": 1}])
        result = sync_chart_dir(self.box.store, self.box.chart, live=False)
        self.assertEqual(result["deleted"], [8])
        self.assertEqual(self.status(), "deleted")
        self.assertIsNone(self.box.store.get_order(8)["close_time"])  # never scored as a trade
        self.box.write("orders.json", {"orders": [self.pending]})  # another, stale folder
        self.box.events([])
        sync_chart_dir(self.box.store, self.box.chart, live=False)
        self.assertEqual(self.status(), "deleted")

    def test_absent_from_live_orders_marks_deleted(self) -> None:
        self.box.write("orders.json", {"orders": []})
        sync_chart_dir(self.box.store, self.box.chart, live=False)
        self.assertEqual(self.status(), "open")  # a stale folder proves nothing
        self.box.write("orders.json", {})  # mid-write read: no list, no verdict
        sync_chart_dir(self.box.store, self.box.chart, live=True)
        self.assertEqual(self.status(), "open")
        self.box.write("orders.json", {"orders": []})
        sync_chart_dir(self.box.store, self.box.chart, live=True)
        self.assertEqual(self.status(), "deleted")

    def test_filled_then_closed_is_not_deleted(self) -> None:
        self.box.write("orders.json", {"orders": [dict(self.pending, type="sell", open_time=1_790_583_500)]})
        sync_chart_dir(self.box.store, self.box.chart, live=True)
        self.assertEqual(self.box.store.get_order(8)["type"], "sell")  # the fill updates the ticket
        self.box.write("orders.json", {"orders": []})
        sync_chart_dir(self.box.store, self.box.chart, live=True)
        self.assertEqual(self.status(), "open")  # market closes come from history.json


class TestManualLinks(unittest.TestCase):
    def setUp(self) -> None:
        self.box = _Outbox()
        self._prev = settings.mt4_files_dir
        settings.mt4_files_dir = str(self.box.root)

    def tearDown(self) -> None:
        settings.mt4_files_dir = self._prev
        self.box.close()

    def _selected(self, *flags: bool) -> None:
        hline = {"name": "S1", "type": "hline", "p1": 1.1353, "t1": 0, "t2": 0, "t3": 0,
                 "p2": 0, "p3": 0, "color": 65535, "style": 2, "width": 1, "text": "", "tooltip": ""}
        objs = [dict(CHANNEL, selected=flags[0]), dict(hline, selected=flags[1])]
        self.box.chart_objects(objs)

    def test_link_selected_survives_sync_and_clears_review(self) -> None:
        self.box.write("orders.json", {"orders": [_order()]})
        self._selected(True, False)
        sync_chart_dir(self.box.store, self.box.chart, live=True)
        self.box.store.upsert_review({"episode_id": "EURUSD:7", "model_fit": 0.5})
        with mock.patch.object(mt4_bridge, "check_chart", return_value=(True, "")):
            out = trader_journal.link_selected(self.box.store, "EUR_USD", "H1")
        self.assertEqual(out["ticket"], 7)
        self.assertEqual(out["linked"], ["Channel 61631"])
        self.assertIsNone(self.box.store.get_review("EURUSD:7"))
        # A later sync with nothing selected must keep the manual link set.
        self._selected(False, False)
        sync_chart_dir(self.box.store, self.box.chart, live=True)
        links = self.box.store.links_for(7)
        self.assertEqual([(l["object_name"], l["source"]) for l in links], [("Channel 61631", "manual")])
        self.assertEqual(links[0]["object"]["p3"], 1.15597)
        ep = self.box.store.get_episode("EURUSD:7")
        self.assertEqual([o["name"] for o in json.loads(ep["objects_json"])], ["Channel 61631"])

    def test_linking_chart_owns_episode_timeframe(self) -> None:
        self.box.write("orders.json", {"orders": [_order()]})
        self._selected(True, False)
        sync_chart_dir(self.box.store, self.box.chart, live=True)
        ep = self.box.store.get_episode("EURUSD:7")
        ep["timeframe"] = "D1"  # first seen on a stale D1 folder
        self.box.store.upsert_episode(ep)
        self.assertEqual(self.box.store.get_episode("EURUSD:7")["timeframe"], "D1")
        self.box.store.upsert_review({"episode_id": "EURUSD:7", "model_fit": None})
        with mock.patch.object(mt4_bridge, "check_chart", return_value=(True, "")):
            trader_journal.link_selected(self.box.store, "EUR_USD", "H1", 7)
        self.assertEqual(self.box.store.get_episode("EURUSD:7")["timeframe"], "H1")
        self.assertIsNone(self.box.store.get_review("EURUSD:7"))
        sync_chart_dir(self.box.store, self.box.chart, live=True)
        self.assertEqual(self.box.store.get_episode("EURUSD:7")["timeframe"], "H1")

    def test_link_requires_selection_and_flag(self) -> None:
        self.box.write("orders.json", {"orders": [_order()]})
        sync_chart_dir(self.box.store, self.box.chart, live=True)
        with mock.patch.object(mt4_bridge, "check_chart", return_value=(True, "")):
            self._selected(False, False)
            with self.assertRaisesRegex(trader_journal.TraderJournalError, "select objects"):
                trader_journal.link_selected(self.box.store, "EUR_USD", "H1")
            self.box.chart_objects([CHANNEL])  # v1.06 export: no 'selected' key
            with self.assertRaisesRegex(trader_journal.TraderJournalError, "v1.07"):
                trader_journal.link_selected(self.box.store, "EUR_USD", "H1")


class TestReviewTimeBase(unittest.TestCase):
    def test_fetch_window_is_utc_and_bars_shift_to_broker(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        try:
            store = TraderStore(Path(tmp.name) / "t.sqlite")
            open_b, close_b = 1_790_581_976, 1_790_613_244
            store.upsert_order(
                _order(close_time=close_b, close_price=1.136, profit=263.0),
                status="closed",
                utc_offset=OFFSET,
            )
            store.upsert_episode(
                {"id": "EURUSD:7", "ticket": 7, "symbol": "EURUSD", "timeframe": "H1",
                 "open_time": open_b, "close_time": close_b, "objects_json": "[]", "brief": "x"}
            )
            calls: list[tuple] = []

            async def fetch(inst, gran, frm, to):
                calls.append((inst, gran, frm, to))
                return [{"time": frm + 3600, "open": 1.138, "high": 1.139, "low": 1.137, "close": 1.1375}]

            asyncio.run(review_pending(store, fetch_bars=fetch))
            self.assertEqual(calls[0][:3], ("EUR_USD", "H1", open_b - OFFSET))
            diag = store.get_review("EURUSD:7")["diagnostics"]
            # The UTC bar at open+1h lands inside the broker-time hold window.
            self.assertEqual(diag["hold_bars"], 1)
        finally:
            tmp.cleanup()


class TestAnalyses(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TraderStore(Path(self.tmp.name) / "t.sqlite")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_add_validates_and_stays_out_of_ingest(self) -> None:
        with self.assertRaises(trader_journal.TraderJournalError):
            trader_journal.add_analysis(self.store, instrument="EUR_USD", kind="gut", body_md="x")
        with self.assertRaises(mt4_bridge.Mt4BridgeError):
            trader_journal.add_analysis(
                self.store, instrument="EUR_USD", kind="chart_read", body_md="x",
                levels=[{"name": "bad", "type": "channel", "t1": 1, "p1": 1.1}],
            )
        row = trader_journal.add_analysis(
            self.store, instrument="EUR_USD", timeframe="H1", kind="chart_read",
            body_md="UNIQUE-ANALYSIS-TEXT", refs=["Channel 61631"],
            levels=[{"name": "T1", "type": "hline", "p1": 1.1353, "color": "green"}],
        )
        self.assertEqual(row["symbol"], "EURUSD")
        self.assertEqual(row["levels"][0]["p1"], 1.1353)
        self.assertNotIn("UNIQUE-ANALYSIS-TEXT", build_corpus_text(self.store))

    def test_pre_trade_auto_attaches_to_next_ticket(self) -> None:
        now = int(time.time())
        pre = self.store.add_analysis(symbol="EURUSD", kind="pre_trade", body_md="plan", created_ts=now - 3600)
        old = self.store.add_analysis(symbol="EURUSD", kind="pre_trade", body_md="stale", created_ts=now - 3 * 86400)
        other = self.store.add_analysis(symbol="GBPUSD", kind="pre_trade", body_md="gbp", created_ts=now - 60)
        self.store.upsert_order(_order(open_time=now + OFFSET), status="open", utc_offset=OFFSET)
        attached = self.store.attach_recent_analyses(7, "EURUSD", now)
        self.assertEqual(attached, [pre["id"]])
        rec = self.store.get_record(7)
        self.assertEqual([a["id"] for a in rec["analyses"]], [pre["id"]])
        self.assertEqual(rec["analyses"][0]["attach_reason"], "auto_window")
        self.assertIsNone(self.store.get_analysis(old["id"])["ticket"])
        self.assertIsNone(self.store.get_analysis(other["id"])["ticket"])


class TestReplayObjects(unittest.TestCase):
    def test_captured_conversion(self) -> None:
        conv = mt4_bridge.captured_to_bridge(CHANNEL, "p.")
        self.assertEqual(conv["color"], "#ff0000")
        self.assertEqual(conv["style"], "solid")
        self.assertEqual((conv["t3"], conv["p3"]), (CHANNEL["t3"], CHANNEL["p3"]))
        norm = mt4_bridge._normalize_object(conv, "p.")
        self.assertEqual(norm["type"], "channel")
        self.assertEqual(mt4_bridge.mt4_color_hex(65535), "#ffff00")
        self.assertEqual(mt4_bridge.mt4_color_hex(0xFFFFFFFF), "white")
        self.assertIsNone(mt4_bridge.captured_to_bridge({"name": "r", "type": "regression"}, "p."))
        with self.assertRaises(mt4_bridge.Mt4BridgeError):
            mt4_bridge._normalize_object({"name": "c", "type": "channel", "t1": 1, "p1": 1.0}, "")

    def test_trade_marks(self) -> None:
        order = _order(
            close_time=1_790_613_244, close_price=1.136, profit=263.0,
            sl=1.142, tp=1.136, sl_open=1.14014, tp_open=1.13235,
        )
        revs = [{"ts": 1_790_582_990, "sl": 1.142, "tp": 1.136, "source": "event"}]
        levels = [[{"name": "sbox.plan.T1", "type": "hline", "p1": 1.1353, "color": "green"}]]
        objs, skipped = mt4_bridge.replay_objects(
            order, [CHANNEL, {"name": "x", "type": "ellipse"}], revisions=revs, levels=levels
        )
        names = {o["name"]: o for o in objs}
        self.assertEqual(skipped, ["x (ellipse)"])
        self.assertIn("sbox.replay.7.m.Channel_61631", names)
        self.assertEqual(names["sbox.replay.7.entry"]["arrow_code"], 242)
        self.assertEqual(names["sbox.replay.7.exit"]["arrow_code"], 252)
        planned, moved = names["sbox.replay.7.sl0"], names["sbox.replay.7.sl1"]
        self.assertEqual((planned["p1"], planned["style"], planned["t2"]), (1.14014, "dash", 1_790_582_990))
        self.assertEqual((moved["p1"], moved["style"], moved["t2"]), (1.142, "solid", 1_790_613_244))
        self.assertIn("sbox.replay.7.a0.T1", names)
        for obj in objs:  # everything must pass the bridge schema
            mt4_bridge._normalize_object(obj, "sbox.replay.7.")


class TestDashboardTrader(unittest.TestCase):
    def setUp(self) -> None:
        from fastapi.testclient import TestClient

        from dashboard.app import app

        self.tmp = tempfile.TemporaryDirectory()
        self.store = TraderStore(Path(self.tmp.name) / "t.sqlite")
        app.state.trader_store = self.store
        self.app = app
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.app.state.trader_store = None
        self.tmp.cleanup()

    def test_list_get_and_draw(self) -> None:
        self.assertEqual(self.client.get("/api/trader/trades").json(), {"trades": []})
        self.assertEqual(self.client.get("/api/trader/trades/7").status_code, 404)
        self.store.upsert_order(_order(), status="open", utc_offset=OFFSET)
        self.store.add_analysis(symbol="EURUSD", kind="in_trade", body_md="hold", ticket=7)
        rows = self.client.get("/api/trader/trades").json()["trades"]
        self.assertEqual((rows[0]["ticket"], rows[0]["side"], rows[0]["analyses"]), (7, "short", 1))
        rec = self.client.get("/api/trader/trades/7").json()
        self.assertEqual(rec["analyses"][0]["body_md"], "hold")
        with mock.patch.object(
            mt4_bridge, "apply_replay", return_value={"ok": True, "objects_written": 4, "timeframe": "H1", "skipped": []}
        ) as draw:
            res = self.client.post("/api/trader/trades/7/draw?analyses=false")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(draw.call_args.kwargs["levels"], [])
        with mock.patch.object(mt4_bridge, "apply_replay", return_value={"ok": False, "error": "no chart"}):
            self.assertEqual(self.client.post("/api/trader/trades/7/draw").status_code, 409)


if __name__ == "__main__":
    unittest.main()
