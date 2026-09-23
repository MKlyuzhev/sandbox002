"""Unit tests for app.regime_change (no network)."""

from __future__ import annotations

import unittest

from app import indicators, regime_change
from app.regime_change import EVIDENCE_CATALOG
from tests.test_structure import channel_rally_bars, reversal_bars


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


def _bar(i: int, o: float, h: float, l: float, c: float, v: int = 1500) -> dict:
    day = f"2024-02-{i - 23:02d}" if i >= 24 else f"2024-01-{i + 1:02d}"
    return {
        "time": f"{day}T00:00:00Z",
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
        "complete": True,
    }


def fresh_reversal_bars() -> list[dict]:
    """Long HH/HL uptrend that rolls over in the FINAL bars.

    ``reversal_bars`` breaks 18 bars before its last bar, which is past the
    recency gate; this fixture keeps the break adjacent to the decision bar.
    """
    bars: list[dict] = []
    price = 1.1000
    for k in range(34):
        if k % 4 == 3:
            c = price - 0.0018
            bars.append(_bar(k, price, price + 0.0004, c - 0.0006, c))
        else:
            c = price + 0.0035
            bars.append(_bar(k, price, c + 0.0006, price - 0.0004, c))
        price = c
    for k, drop in enumerate((0.0030, 0.0075, 0.0090), start=34):
        c = price - drop
        bars.append(_bar(k, price, price + 0.0005, c - 0.0008, c, 2600))
        price = c
    return bars


class TestDetect(unittest.TestCase):
    def test_stable_on_flat(self) -> None:
        block = regime_change.detect(_flat(), instrument="EUR_USD")
        self.assertEqual(block["state"], "stable")
        self.assertEqual(block["score"], 0.0)
        self.assertEqual(block["evidence"], [])

    def test_stale_event_is_not_evidence(self) -> None:
        """A break 18 bars behind the decision bar is history, not confirmation."""
        block = regime_change.detect(reversal_bars(), instrument="EUR_USD")
        self.assertEqual(block["state"], "stable")
        self.assertEqual(block["evidence"], [])
        self.assertEqual(block["recency_bars"], regime_change.EVENT_RECENCY_BARS)

    def test_unbounded_recency_restores_stale_evidence(self) -> None:
        """The gate is the only thing suppressing it; ages prove staleness."""
        block = regime_change.detect(reversal_bars(), instrument="EUR_USD", recency=250)
        self.assertEqual(block["state"], "confirming")
        ages = {e["signal"]: e["age"] for e in block["evidence"]}
        self.assertIn("trendline_break_valid", ages)
        self.assertGreater(ages["trendline_break_valid"], regime_change.EVENT_RECENCY_BARS)

    def test_gated_evidence_is_always_fresh(self) -> None:
        """Invariant: any dated evidence under the default gate is recent."""
        for bars in (reversal_bars(), fresh_reversal_bars()):
            for end in range(32, len(bars) + 1):
                block = regime_change.detect(bars[:end], instrument="EUR_USD")
                for ev in block["evidence"]:
                    if ev["age"] is not None:
                        self.assertLessEqual(ev["age"], regime_change.EVENT_RECENCY_BARS)
                        self.assertGreaterEqual(ev["age"], 0)

    def test_fresh_reversal_confirms(self) -> None:
        block = regime_change.detect(fresh_reversal_bars()[:35], instrument="EUR_USD")
        self.assertIn(block["state"], ("confirming", "confirmed"))
        self.assertEqual(block["direction_to"], "down")
        self.assertGreater(block["score"], 0.0)

    def test_far_rail_failure_is_dated_and_gated(self) -> None:
        """The warning carries the failing swing's age, so the gate applies."""
        bars = channel_rally_bars(1.1260)
        block = regime_change.detect(bars, instrument="EUR_USD", recency=250)
        by_signal = {e["signal"]: e for e in block["evidence"]}
        self.assertIn("channel_far_rail_failure", by_signal)
        self.assertIsNotNone(by_signal["channel_far_rail_failure"]["age"])

        # A steady drift up to the rail retires the warning: the newest leg no
        # longer falls short, so there is no failed leg to report.
        tail = list(bars)
        c = tail[-1]["close"]
        for k in range(8):
            c += 0.0022
            tail.append(
                {
                    "time": f"2024-06-{k + 1:02d}T00:00:00Z",
                    "open": c - 0.0018,
                    "high": c + 0.0004,
                    "low": c - 0.0022,
                    "close": c,
                    "volume": 1200,
                    "complete": True,
                }
            )
        recovered = regime_change.detect(tail, instrument="EUR_USD", recency=250)
        self.assertNotIn(
            "channel_far_rail_failure",
            [e["signal"] for e in recovered["evidence"]],
        )

    def test_stale_trendline_supplies_no_measured_move(self) -> None:
        gated = regime_change.detect(reversal_bars(), instrument="EUR_USD")
        self.assertIsNone(gated["measured_move"])

    def test_evidence_citation_lockstep(self) -> None:
        block = regime_change.detect(reversal_bars(), instrument="EUR_USD", recency=250)
        self.assertTrue(block["evidence"])
        for ev in block["evidence"]:
            spec = EVIDENCE_CATALOG[ev["signal"]]
            self.assertEqual(ev["citation"]["source"], spec["source"])
            self.assertEqual(ev["citation"]["chunk_index"], spec["chunk_index"])
            self.assertEqual(ev["stage"], spec["stage"])

    def test_citations_unique(self) -> None:
        block = regime_change.detect(reversal_bars(), instrument="EUR_USD", recency=250)
        keys = [(c["source"], c["chunk_index"]) for c in block["citations"]]
        self.assertEqual(len(keys), len(set(keys)))

    def test_too_few_bars_raises(self) -> None:
        with self.assertRaises(indicators.IndicatorError):
            regime_change.detect(_flat(5))


class TestRecencyHelpers(unittest.TestCase):
    def test_age(self) -> None:
        self.assertEqual(regime_change._age(39, 21), 18)
        self.assertIsNone(regime_change._age(39, None))

    def test_fresh(self) -> None:
        self.assertTrue(regime_change._fresh(0, 5))
        self.assertTrue(regime_change._fresh(5, 5))
        self.assertFalse(regime_change._fresh(6, 5))
        self.assertFalse(regime_change._fresh(None, 5))


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
