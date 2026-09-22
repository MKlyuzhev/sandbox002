"""Tests for trader outbox parse, object↔order bind, and post-trade review."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app import trader_episodes, trader_outbox, trader_review
from app.trader_store import TraderStore
from agent.trader_distill import distill
from agent.trader_sync import sync_chart_dir


def _obj(**kwargs):
    data = {
        "name": "channel_1",
        "type": "channel",
        "t1": 1000,
        "t2": 2000,
        "t3": 1500,
        "p1": 1.1000,
        "p2": 1.1100,
        "p3": 1.1050,
        "text": "",
        "tooltip": "role=channel",
    }
    data.update(kwargs)
    return data


def _bar(ts: int, o: float, h: float, l: float, c: float) -> dict:
    return {"time": ts, "open": o, "high": h, "low": l, "close": c}


class TestOutboxParse(unittest.TestCase):
    def test_role_from_tooltip(self) -> None:
        tags = trader_outbox.parse_role_tags("", "role=sr tf=D1 note=prior high")
        self.assertEqual(tags["role"], "sr")
        self.assertEqual(tags["tf"], "D1")
        self.assertIn("prior high", tags["note"])

    def test_unknown_without_tag(self) -> None:
        tags = trader_outbox.parse_role_tags("hello", "")
        self.assertEqual(tags["role"], "unknown")

    def test_skips_sbox_prefix(self) -> None:
        objs = trader_outbox.filter_user_objects(
            [
                {"name": "sbox.regime.sma", "type": "trend", "text": "", "tooltip": ""},
                {"name": "hline_1", "type": "hline", "p1": 1.2, "tooltip": "role=sr"},
            ]
        )
        self.assertEqual(len(objs), 1)
        self.assertEqual(objs[0]["role"], "sr")


class TestBind(unittest.TestCase):
    def test_sl_price_link(self) -> None:
        order = {
            "ticket": 1,
            "symbol": "GBPUSD",
            "type": "sell",
            "open_price": 1.2500,
            "open_time": 1500,
            "sl": 1.2550,
            "tp": 1.2400,
            "comment": "",
        }
        objects = [
            _obj(name="stop_line", type="hline", p1=1.2550, tooltip="role=stop"),
            _obj(name="other", type="hline", p1=1.3000, tooltip="role=sr"),
        ]
        links = trader_episodes.bind_objects(order, objects)
        names = {link["object_name"] for link in links}
        self.assertIn("stop_line", names)
        self.assertTrue(any(link["reason"] == "sl_price" for link in links))

    def test_comment_name(self) -> None:
        order = {
            "ticket": 2,
            "symbol": "GBPUSD",
            "type": "buy",
            "open_price": 1.25,
            "open_time": 1500,
            "sl": 1.24,
            "tp": 1.27,
            "comment": "fade channel_1",
        }
        links = trader_episodes.bind_objects(order, [_obj()])
        self.assertEqual(links[0]["reason"], "comment")


class TestReview(unittest.TestCase):
    def test_channel_held_vs_sliced(self) -> None:
        obj = _obj(
            type="rectangle",
            p1=1.10,
            p2=1.12,
            t1=1000,
            t2=3000,
            tooltip="role=channel",
        )
        order = {
            "symbol": "GBPUSD",
            "type": "buy",
            "open_price": 1.11,
            "open_time": 1100,
            "close_time": 1900,
            "close_price": 1.115,
            "sl": 1.09,
            "tp": 1.13,
            "sl_open": 1.09,
            "tp_open": 1.13,
        }
        held_bars = [
            _bar(1200, 1.11, 1.112, 1.108, 1.111),
            _bar(1400, 1.111, 1.114, 1.109, 1.113),
            _bar(1600, 1.113, 1.116, 1.111, 1.114),
        ]
        held = trader_review.review_trade(
            order=order, objects=[obj], bars=held_bars
        )
        self.assertIsNotNone(held["model_fit"])
        self.assertGreaterEqual(held["model_fit"], 0.8)
        self.assertIn("structure_held", held["reasons"])

        sliced_bars = [
            _bar(1200, 1.11, 1.112, 1.108, 1.111),
            _bar(1400, 1.11, 1.12, 1.05, 1.06),
        ]
        sliced = trader_review.review_trade(
            order=order, objects=[obj], bars=sliced_bars
        )
        self.assertLess(sliced["model_fit"], held["model_fit"])
        self.assertIn("structure_broke_immediately", sliced["reasons"])

    def test_chase_vs_rail_tag(self) -> None:
        hline = {
            "name": "res",
            "type": "hline",
            "p1": 1.2000,
            "tooltip": "role=sr",
            "text": "",
        }
        near = {
            "symbol": "GBPUSD",
            "type": "sell",
            "open_price": 1.2005,
            "open_time": 1000,
            "close_time": 2000,
            "close_price": 1.1950,
            "sl": 1.2060,
            "tp": 1.1880,
            "sl_open": 1.2060,
            "tp_open": 1.1880,
        }
        far = dict(near, open_price=1.2100)
        bars = [_bar(1500, 1.20, 1.201, 1.199, 1.200)]
        tagged = trader_review.review_trade(order=near, objects=[hline], bars=bars)
        chased = trader_review.review_trade(order=far, objects=[hline], bars=bars)
        self.assertIn("entry_near_rail", tagged["reasons"])
        self.assertIn("chase", chased["reasons"])
        self.assertGreater(tagged["entry_quality"], chased["entry_quality"])

    def test_stop_widen_vs_target_hit(self) -> None:
        obj = _obj(type="hline", p1=1.20, tooltip="role=sr")
        base = {
            "symbol": "GBPUSD",
            "type": "sell",
            "open_price": 1.2000,
            "open_time": 1000,
            "close_time": 2000,
            "sl": 1.2060,
            "tp": 1.1880,
            "sl_open": 1.2060,
            "tp_open": 1.1880,
        }
        bars = [_bar(1500, 1.20, 1.201, 1.189, 1.190)]
        target = trader_review.review_trade(
            order={**base, "close_price": 1.1880}, objects=[obj], bars=bars
        )
        widened = trader_review.review_trade(
            order={**base, "close_price": 1.2070, "sl": 1.2100},
            objects=[obj],
            bars=bars,
        )
        self.assertIn("target_hit", target["reasons"])
        self.assertIn("stop_widened", widened["reasons"])
        self.assertGreater(target["exit_quality"], widened["exit_quality"])

    def test_no_structure_null_model(self) -> None:
        order = {
            "symbol": "GBPUSD",
            "type": "buy",
            "open_price": 1.1,
            "open_time": 1,
            "close_time": 2,
            "close_price": 1.11,
            "sl": 1.09,
            "tp": 1.13,
        }
        out = trader_review.review_trade(order=order, objects=[], bars=[])
        self.assertIsNone(out["model_fit"])
        self.assertIn("no_structure", out["reasons"])


class TestStoreSync(unittest.TestCase):
    def test_definitions_and_sync_dir(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        try:
            db = Path(tmp.name) / "t.sqlite"
            store = TraderStore(db)
            store.set_definition("macro_trend", granularity="W1", notes="weekly structure")
            defs = store.get_definitions()
            self.assertEqual(defs[0]["term"], "macro_trend")
            self.assertEqual(defs[0]["granularity"], "W1")

            chart = Path(tmp.name) / "GBPUSD_H1"
            chart.mkdir()
            (chart / "heartbeat.json").write_text("{}", encoding="utf-8")
            (chart / "chart.json").write_text(
                json.dumps(
                    {
                        "symbol": "GBPUSD",
                        "timeframe": "H1",
                        "time_current": 2000,
                        "objects": [
                            {
                                "name": "hline_1",
                                "type": "hline",
                                "p1": 1.2550,
                                "tooltip": "role=stop",
                                "text": "",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (chart / "orders.json").write_text(
                json.dumps({"orders": []}), encoding="utf-8"
            )
            (chart / "history.json").write_text(
                json.dumps(
                    {
                        "orders": [
                            {
                                "ticket": 9,
                                "symbol": "GBPUSD",
                                "type": "sell",
                                "lots": 0.1,
                                "open_price": 1.2500,
                                "open_time": 1500,
                                "sl": 1.2550,
                                "tp": 1.2400,
                                "close_price": 1.2400,
                                "close_time": 1800,
                                "profit": 10.0,
                                "comment": "",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            result = sync_chart_dir(store, chart)
            self.assertEqual(result["objects"], 1)
            ep = store.get_episode("GBPUSD:9")
            self.assertIsNotNone(ep)
            links = store.links_for(9)
            self.assertTrue(any(link["object_name"] == "hline_1" for link in links))
        finally:
            tmp.cleanup()

    def test_distill_hypotheses_only(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        try:
            store = TraderStore(Path(tmp.name) / "t.sqlite")
            for ticket in (1, 2):
                order = {
                    "ticket": ticket,
                    "symbol": "GBPUSD",
                    "type": "sell",
                    "lots": 0.1,
                    "open_price": 1.25,
                    "open_time": 1000 + ticket,
                    "sl": 1.26,
                    "tp": 1.23,
                    "close_price": 1.26,
                    "close_time": 2000,
                    "comment": "",
                }
                store.upsert_order(order, status="closed")
                store.upsert_episode(
                    {
                        "id": f"GBPUSD:{ticket}",
                        "ticket": ticket,
                        "symbol": "GBPUSD",
                        "timeframe": "H1",
                        "open_time": 1000,
                        "close_time": 2000,
                        "objects_json": json.dumps(
                            [{"name": "h", "type": "hline", "role": "sr"}]
                        ),
                    }
                )
                store.upsert_review(
                    {
                        "episode_id": f"GBPUSD:{ticket}",
                        "model_fit": 0.2,
                        "entry_quality": 0.4,
                        "exit_quality": 0.3,
                        "reasons": ["chase"],
                        "diagnostics": {},
                        "brief": "chase",
                    }
                )
            rules = distill(store, min_cluster=2)
            self.assertTrue(rules)
            self.assertTrue(all(r["status"] == "hypothesis" for r in rules))
            self.assertEqual(store.list_rules(status="accepted"), [])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
