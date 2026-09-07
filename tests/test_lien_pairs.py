"""Unit tests for the lien-fx research pair pool (no network)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from agent.lien_pairs import (
    ALIASES,
    CH4_SESSION_BOARD,
    HISTORICAL,
    RESEARCH_POOL,
    USD_MAJORS,
    expand_instruments,
)
from agent.scan import DEFAULT_UNIVERSE, MAX_INSTRUMENTS, parse_instruments

_JSON = Path(__file__).resolve().parent.parent / "data" / "lien_fx_pairs.json"


class TestLienPairs(unittest.TestCase):
    def test_oanda_shape(self) -> None:
        for name in RESEARCH_POOL + CH4_SESSION_BOARD + USD_MAJORS:
            base, quote = name.split("_")
            self.assertEqual(len(base), 3)
            self.assertEqual(len(quote), 3)
            self.assertNotEqual(base, quote)

    def test_subsets(self) -> None:
        pool = set(RESEARCH_POOL)
        self.assertTrue(set(USD_MAJORS) <= pool)
        self.assertTrue(set(CH4_SESSION_BOARD) <= pool)
        self.assertEqual(len(CH4_SESSION_BOARD), 12)
        self.assertLessEqual(len(RESEARCH_POOL), MAX_INSTRUMENTS)
        self.assertEqual(len(set(RESEARCH_POOL)), len(RESEARCH_POOL))
        self.assertTrue(set(HISTORICAL).isdisjoint(pool))
        self.assertIn("USD_SGD", RESEARCH_POOL)

    def test_scan_default_is_usd_majors(self) -> None:
        self.assertEqual(DEFAULT_UNIVERSE, USD_MAJORS)
        self.assertEqual(parse_instruments(""), list(USD_MAJORS))

    def test_alias_ch4_fits_scan_cap(self) -> None:
        names = parse_instruments("lien-fx-ch4")
        self.assertEqual(names, list(CH4_SESSION_BOARD))
        self.assertEqual(len(names), 12)

    def test_alias_full_pool_fits_scan_cap(self) -> None:
        self.assertLessEqual(len(RESEARCH_POOL), MAX_INSTRUMENTS)
        self.assertEqual(expand_instruments("lien-fx"), list(RESEARCH_POOL))
        self.assertEqual(expand_instruments("usd-majors"), list(USD_MAJORS))
        self.assertIsNone(expand_instruments("EUR_USD,GBP_USD"))

    def test_json_matches_module(self) -> None:
        payload = json.loads(_JSON.read_text(encoding="utf-8"))
        json_pool = [row["instrument"] for row in payload["research_pool"]]
        self.assertEqual(set(json_pool), set(RESEARCH_POOL))
        self.assertEqual(payload["aliases"]["usd-majors"], list(USD_MAJORS))
        self.assertEqual(payload["aliases"]["lien-fx-ch4"], list(CH4_SESSION_BOARD))
        self.assertEqual(set(ALIASES), {"usd-majors", "lien-fx-ch4", "lien-fx"})


if __name__ == "__main__":
    unittest.main()
