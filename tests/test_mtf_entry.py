"""MTF entry read: grid drift, projections, exposure, active pairs."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app import mtf_channel as mc, mtf_entry, trader_journal
from app.trader_store import TraderStore


def _grid(p1: float, slope: float, width: float, tf: mc.TF = mc.D1) -> mc.Grid:
    base = mc.Line(0, p1, 10, p1 + 10 * slope)
    return mc._grid(base, width, {}, "test", tf)


def _days(n: int) -> list[dict]:
    """n weekday D1 bars from Mon 2026-09-07 (broker time)."""
    t = int(datetime(2026, 9, 7, tzinfo=timezone.utc).timestamp())
    out = []
    while len(out) < n:
        if datetime.fromtimestamp(t, timezone.utc).weekday() < 5:
            out.append({"time": t, "open": 1, "high": 1, "low": 1, "close": 1})
        t += 86400
    return out


class TestGridStatus(unittest.TestCase):
    def test_same_grid_matches(self) -> None:
        g = _grid(1.12, -0.0002, 0.05)
        self.assertEqual(mtf_entry.grid_status(g, _grid(1.12, -0.0002, 0.05), 40, 5)["status"], "matches")

    def test_reanchored_grid_differs(self) -> None:
        # 40 pips on a 500-pip channel: more than the 2% tolerance (10 pips).
        out = mtf_entry.grid_status(_grid(1.124, -0.0002, 0.05), _grid(1.12, -0.0002, 0.05), 40, 5)
        self.assertEqual(out["status"], "differs")
        self.assertAlmostEqual(out["drawn_minus_proposed"]["upper"], 0.004, places=5)

    def test_missing_and_no_proposal(self) -> None:
        g = _grid(1.12, -0.0002, 0.05)
        self.assertEqual(mtf_entry.grid_status(None, g, 40, 5)["status"], "missing")
        self.assertEqual(mtf_entry.grid_status(g, None, 40, 5)["status"], "no_proposal")


class TestProjections(unittest.TestCase):
    def test_next_closes_skip_the_weekend(self) -> None:
        bars = _days(5)  # Mon 09-07 .. Fri 09-11
        rows = mtf_entry.projections(bars, _grid(1.0, 0.001, 0.05), (1, 2), 5)
        self.assertEqual([r["bar"] for r in rows], ["2026-09-14", "2026-09-15"])
        self.assertAlmostEqual(rows[0]["lower"], 1.005)  # index 5
        self.assertAlmostEqual(rows[0]["center"], 1.03)

    def test_in_progress_bar_is_one_ahead(self) -> None:
        bars = _days(5)
        bars[-1]["complete"] = False  # Friday still open
        rows = mtf_entry.projections(bars, _grid(1.0, 0.001, 0.05), (1,), 5)
        self.assertEqual(rows[0]["bar"], "2026-09-11")


class TestExposure(unittest.TestCase):
    def test_nets_currencies_filled_and_pending_apart(self) -> None:
        orders = [
            {"symbol": "EURUSD", "type": "buy_limit", "lots": 0.29, "status": "open"},
            {"symbol": "USDJPY", "type": "sell", "lots": 0.35, "status": "open"},
            {"symbol": "GBPUSD", "type": "buy", "lots": 1.0, "status": "closed"},
        ]
        self.assertEqual(mtf_entry.exposure(orders), {
            "filled": {"USD": -0.35, "JPY": 0.35},
            "pending": {"EUR": 0.29, "USD": -0.29},
        })


class TestActiveInstruments(unittest.TestCase):
    def test_only_pairs_with_an_active_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TraderStore(Path(tmp) / "t.sqlite")
            order = {"type": "buy_limit", "price": 1.113, "sl": 1.109, "tp": 1.119}
            a = trader_journal.add_analysis(store, instrument="EUR_USD", kind="pre_trade",
                                            body_md="# a", order=order)
            sell = {"type": "sell_stop", "price": 157.7, "sl": 158.35, "tp": 156.7}
            b = trader_journal.add_analysis(store, instrument="USD_JPY", kind="pre_trade",
                                            body_md="# b", order=sell)
            trader_journal.set_plan_status(store, b["id"], "cancelled")
            self.assertEqual(mtf_entry.active_instruments(store), ["EUR_USD"])
            self.assertEqual(a["status"], "active")


if __name__ == "__main__":
    unittest.main()
