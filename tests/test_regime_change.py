"""Unit tests for app.regime_change (no network)."""

from __future__ import annotations

import unittest

from app import indicators, regime_change
from app.regime_change import EVIDENCE_CATALOG
from tests.test_structure import reversal_bars


def _flat(n: int = 40) -> list[dict]:
    return [
        {
            "time": f"2024-01-{i + 1:02d}T00:00:00Z",
            "open": 1.10,
            "high": 1.1003,
            "low": 1.0997,
            "close": 1.10,
            "volume": 1000,
            "complete": True,
        }
        for i in range(n)
    ]


class TestDetect(unittest.TestCase):
    def test_stable_on_flat(self) -> None:
        block = regime_change.detect(_flat(), instrument="EUR_USD")
        self.assertEqual(block["state"], "stable")
        self.assertEqual(block["score"], 0.0)
        self.assertEqual(block["evidence"], [])

    def test_reversal_scenario(self) -> None:
        block = regime_change.detect(reversal_bars(), instrument="EUR_USD")
        self.assertIn(block["state"], ("confirming", "confirmed"))
        self.assertEqual(block["direction_to"], "down")
        signals = [e["signal"] for e in block["evidence"]]
        self.assertIn("trendline_break_valid", signals)
        self.assertGreater(block["score"], 0.0)

    def test_evidence_citation_lockstep(self) -> None:
        block = regime_change.detect(reversal_bars(), instrument="EUR_USD")
        for ev in block["evidence"]:
            spec = EVIDENCE_CATALOG[ev["signal"]]
            self.assertEqual(ev["citation"]["source"], spec["source"])
            self.assertEqual(ev["citation"]["chunk_index"], spec["chunk_index"])
            self.assertEqual(ev["stage"], spec["stage"])

    def test_citations_unique(self) -> None:
        block = regime_change.detect(reversal_bars(), instrument="EUR_USD")
        keys = [(c["source"], c["chunk_index"]) for c in block["citations"]]
        self.assertEqual(len(keys), len(set(keys)))

    def test_too_few_bars_raises(self) -> None:
        with self.assertRaises(indicators.IndicatorError):
            regime_change.detect(_flat(5))


class TestHelpers(unittest.TestCase):
    def test_opposite(self) -> None:
        self.assertEqual(regime_change._opposite("up"), "down")
        self.assertEqual(regime_change._opposite("down"), "up")
        self.assertIsNone(regime_change._opposite(None))

    def test_vote_direction_weighted(self) -> None:
        evidence = [
            {"direction": "down", "weight": 0.2},
            {"direction": "up", "weight": 0.15},
        ]
        self.assertEqual(regime_change._vote_direction(evidence, "up"), "down")

    def test_vote_direction_tie_uses_trend_opposite(self) -> None:
        evidence = [
            {"direction": "down", "weight": 0.15},
            {"direction": "up", "weight": 0.15},
        ]
        self.assertEqual(regime_change._vote_direction(evidence, "up"), "down")


if __name__ == "__main__":
    unittest.main()
