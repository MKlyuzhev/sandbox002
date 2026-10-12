"""Plans: order specs on pre-trade analyses, sizing, checks, attach, dashboard."""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import mtf_channel, trade_plan, trader_journal
from app.trader_store import TraderStore

SELL_STOP = {"type": "sell_stop", "price": 157.70, "sl": 158.35, "tp": 156.70,
             "expiry": "2026-10-14 23:59", "risk": {"amount": 23000, "ccy": "JPY"},
             "checks": [{"label": "D1 rail holds", "timeframe": "D1", "line": "upper",
                         "rule": "close_below"}]}


def _ticket_order(ticket: int, **kw) -> dict:
    base = {"ticket": ticket, "symbol": "USDJPY", "type": "sell_stop", "lots": 0.35,
            "open_price": 157.70, "open_time": 1_791_773_000, "sl": 158.35, "tp": 156.70}
    base.update(kw)
    return base


class _Store(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TraderStore(Path(self.tmp.name) / "trader.sqlite")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def plan(self, order: dict | None = SELL_STOP, **kw) -> dict:
        return trader_journal.add_analysis(
            self.store, instrument="USD_JPY", kind="pre_trade", body_md="# plan", order=order, **kw
        )


class TestValidate(unittest.TestCase):
    def test_normalizes(self) -> None:
        o = trade_plan.validate_order(SELL_STOP)
        self.assertEqual(o["type"], "sell_stop")
        self.assertEqual(o["expiry"], 1792022340)  # 2026-10-14 23:59 broker
        self.assertEqual(o["checks"][0]["leg"], "current")
        self.assertEqual(o["risk"], {"amount": 23000.0, "ccy": "JPY"})

    def test_rejects_wrong_side(self) -> None:
        with self.assertRaises(trade_plan.PlanError):
            trade_plan.validate_order(dict(SELL_STOP, sl=157.0))
        with self.assertRaises(trade_plan.PlanError):
            trade_plan.validate_order({"type": "buy_limit", "price": 1.114, "sl": 1.12, "tp": 1.13})

    def test_rejects_bad_check_and_type(self) -> None:
        with self.assertRaises(trade_plan.PlanError):
            trade_plan.validate_order(dict(SELL_STOP, type="sell"))
        with self.assertRaises(trade_plan.PlanError):
            trade_plan.validate_order(dict(SELL_STOP, checks=[{"timeframe": "D1", "line": "top", "rule": "close_below"}]))
        with self.assertRaises(mtf_channel.ChannelError):
            trade_plan.validate_order(dict(SELL_STOP, checks=[{"timeframe": "M7", "line": "upper", "rule": "close_below"}]))


class TestStore(_Store):
    def test_plan_round_trip_and_supersede(self) -> None:
        old = self.plan()
        new = self.plan(dict(SELL_STOP, price=157.60), supersedes=old["id"])
        self.assertEqual(new["status"], "active")
        self.assertEqual(new["order"]["price"], 157.60)
        self.assertEqual(self.store.get_analysis(old["id"])["status"], "superseded")
        self.assertEqual([p["id"] for p in self.store.list_plans("USDJPY")], [new["id"], old["id"]])

    def test_order_only_on_pre_trade(self) -> None:
        with self.assertRaises(trader_journal.TraderJournalError):
            trader_journal.add_analysis(self.store, instrument="USD_JPY", kind="chart_read",
                                        body_md="x", order=SELL_STOP)

    def test_status(self) -> None:
        p = self.plan()
        self.assertEqual(trader_journal.set_plan_status(self.store, p["id"], "cancelled")["status"], "cancelled")
        with self.assertRaises(trader_journal.TraderJournalError):
            trader_journal.set_plan_status(self.store, p["id"], "filled")

    def test_migrates_an_old_database(self) -> None:
        path = Path(self.tmp.name) / "old.sqlite"
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE analyses (id INTEGER PRIMARY KEY AUTOINCREMENT, ticket INTEGER,"
                         " symbol TEXT NOT NULL, timeframe TEXT, kind TEXT NOT NULL, author TEXT NOT NULL,"
                         " model TEXT, created_ts INTEGER NOT NULL, body_md TEXT NOT NULL, refs_json TEXT,"
                         " levels_json TEXT, drawn_prefix TEXT, attach_reason TEXT)")
            conn.execute("INSERT INTO analyses (symbol, kind, author, created_ts, body_md)"
                         " VALUES ('USDJPY', 'pre_trade', 'claude', 1, 'old')")
        rows = TraderStore(path).list_analyses()
        self.assertIsNone(rows[0]["order"])
        self.assertIsNone(rows[0]["status"])


class TestAttach(_Store):
    def test_order_match_beats_the_window_and_skips_superseded(self) -> None:
        superseded = self.plan(None)  # an old text-only plan
        p = self.plan(dict(SELL_STOP), supersedes=superseded["id"])
        self.store.upsert_order(_ticket_order(11), status="open")
        ids = self.store.attach_recent_analyses(11, "USDJPY", 1_791_772_000)
        self.assertEqual(ids, [p["id"]])
        self.assertEqual(self.store.get_analysis(p["id"])["attach_reason"], "order_match")
        self.assertIsNone(self.store.get_analysis(superseded["id"])["ticket"])

    def test_filled_market_ticket_matches_within_slippage(self) -> None:
        p = self.plan()
        self.store.upsert_order(_ticket_order(12, type="sell", open_price=157.67), status="open")
        self.assertEqual(self.store.attach_recent_analyses(12, "USDJPY", 0), [p["id"]])

    def test_other_orders_do_not_take_the_plan(self) -> None:
        p = self.plan()
        note = trader_journal.add_analysis(self.store, instrument="USD_JPY", kind="pre_trade", body_md="note")
        self.store.upsert_order(_ticket_order(13, type="buy_limit", open_price=157.0), status="open")
        ids = self.store.attach_recent_analyses(13, "USDJPY", note["created_ts"] + 10)
        self.assertEqual(ids, [note["id"]])  # the text-only note, by the time window
        self.assertIsNone(self.store.get_analysis(p["id"])["ticket"])


class TestSizing(unittest.TestCase):
    def test_usd_jpy(self) -> None:
        o = trade_plan.validate_order(SELL_STOP)
        jpy = trade_plan.size("USD_JPY", o, 23000, "JPY", {"USD_JPY": 158.0})
        self.assertEqual(jpy["stop_pips"], 65.0)
        self.assertEqual(jpy["pip_value_per_lot"], 1000.0)
        self.assertEqual(jpy["lots"], 0.35)
        usd = trade_plan.size("USD_JPY", o, 145, "USD", {"USD_JPY": 158.0})
        self.assertEqual(usd["lots"], 0.35)  # 145 / (65 * 6.329)
        self.assertAlmostEqual(usd["r_multiple"], 1.54, places=2)

    def test_eur_usd_and_cross(self) -> None:
        o = trade_plan.validate_order({"type": "buy_limit", "price": 1.1140, "sl": 1.1090, "tp": 1.1225})
        s = trade_plan.size("EUR_USD", o, 100, "USD", {"EUR_USD": 1.12})
        self.assertEqual((s["stop_pips"], s["pip_value_per_lot"], s["lots"]), (50.0, 10.0, 0.2))
        x = trade_plan.validate_order({"type": "sell_limit", "price": 170.0, "sl": 170.5, "tp": 169.0})
        s = trade_plan.size("EUR_JPY", x, 100, "USD", {"EUR_JPY": 170.0, "USD_JPY": 158.0})
        self.assertAlmostEqual(s["pip_value_per_lot"], 1000 / 158.0, places=3)
        self.assertEqual(trade_plan.conversion_pairs("EUR_JPY", "USD"), ["EUR_JPY", "JPY_USD", "USD_JPY"])
        with self.assertRaises(trade_plan.PlanError):
            trade_plan.size("EUR_JPY", x, 100, "GBP", {"EUR_JPY": 170.0})

    def test_ticket_text_and_distance(self) -> None:
        o = trade_plan.validate_order(SELL_STOP)
        text = trade_plan.ticket_text("USD_JPY", o, 0.35)
        for part in ("USDJPY  Sell Stop", "At price     157.7", "Stop Loss    158.35",
                     "Volume       0.35", "2026.10.14 23:59"):
            self.assertIn(part, text)
        self.assertEqual(trade_plan.distance_pips("USD_JPY", o, 158.10, 158.12), -40.0)
        self.assertEqual(trade_plan.to_instrument("EURUSD"), "EUR_USD")


class TestChecks(unittest.TestCase):
    def test_close_below_line(self) -> None:
        bars = [{"time": 1_790_000_000 + i * 86400, "close": 158.0 + 0.01 * i, "complete": True} for i in range(5)]
        base = mtf_channel.Line(0, 150.0, 4, 150.0)
        grid = mtf_channel._grid(base, 8.0, {}, "test", mtf_channel.D1)  # upper rail 158.0 flat

        async def fake(_instrument, _check):
            return bars, grid

        o = trade_plan.validate_order(SELL_STOP)
        with mock.patch.object(trade_plan, "_grid_for", fake):
            rows = asyncio.run(trade_plan.run_checks("USD_JPY", o))
        self.assertFalse(rows[0]["ok"])  # last close 158.04 is above the 158.0 rail
        self.assertEqual(rows[0]["line_at_close"], 158.0)


class TestDashboardPlans(_Store):
    def setUp(self) -> None:
        super().setUp()
        from fastapi.testclient import TestClient

        from dashboard.app import app

        app.state.trader_store = self.store
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.addCleanup(setattr, app.state, "trader_store", None)

    def test_list_detail_status_link(self) -> None:
        p = self.plan()
        self.assertEqual(self.client.get("/api/trader/plans").json()["plans"][0]["type"], "sell_stop")

        async def mids(_names):
            return {"USD_JPY": 158.0}

        async def quote(_instrument):
            return {"bid": 158.10, "ask": 158.12, "time": "t"}

        with mock.patch.object(trade_plan, "live_mids", mids), mock.patch.object(trade_plan, "quote", quote):
            r = self.client.get(f"/api/trader/plans/{p['id']}").json()
            self.assertEqual(r["ticket"]["sizing"]["lots"], 0.35)  # the plan's JPY 23,000 default
            self.assertEqual(r["ticket"]["price"]["distance_pips"], -40.0)
            r = self.client.get(f"/api/trader/plans/{p['id']}?risk=145&ccy=usd").json()
            self.assertEqual(r["ticket"]["sizing"]["account_ccy"], "USD")
        self.assertEqual(self.client.get("/api/trader/plans/999").status_code, 404)
        res = self.client.post(f"/api/trader/plans/{p['id']}/status?status=cancelled")
        self.assertEqual(res.json()["status"], "cancelled")
        self.assertEqual(self.client.post(f"/api/trader/plans/{p['id']}/link?ticket=55").status_code, 409)
        self.store.upsert_order(_ticket_order(55), status="open")
        self.assertEqual(self.client.post(f"/api/trader/plans/{p['id']}/link?ticket=55").json()["ticket"], 55)

    def test_no_order_routes(self) -> None:
        from dashboard.app import app

        posts = [r.path for r in app.routes if "POST" in getattr(r, "methods", set()) and "/plans" in r.path]
        self.assertEqual(sorted(posts), ["/api/trader/plans/{plan_id}/draw",
                                         "/api/trader/plans/{plan_id}/link",
                                         "/api/trader/plans/{plan_id}/status"])


if __name__ == "__main__":
    unittest.main()
