"""Unit tests for proposal JSON parsing (no network)."""

from __future__ import annotations

import unittest

from agent.propose import parse_proposal


class TestParseProposal(unittest.TestCase):
    def test_notes_list_from_qwen(self) -> None:
        raw = """
        {
          "thesis": "join the daily uptrend",
          "play_class": "join_trend",
          "side": "long",
          "entry": 1.353,
          "stop": 1.351,
          "target": 1.357,
          "confidence": 0.6,
          "citations": [],
          "notes": ["risk_reversals=unavailable", "implied_vol=unavailable"]
        }
        """
        proposal = parse_proposal(raw)
        self.assertEqual(proposal.play_class, "join_trend")
        self.assertIn("risk_reversals=unavailable", proposal.notes)

    def test_missing_thesis_and_play_class(self) -> None:
        raw = '{"entry": null, "stop": null, "target": null, "side": "none"}'
        proposal = parse_proposal(raw, allowed_play_classes=["join_trend"])
        self.assertEqual(proposal.play_class, "join_trend")
        self.assertEqual(proposal.side, "none")
        self.assertTrue(proposal.thesis)

    def test_confidence_word_high(self) -> None:
        raw = """
        {
          "thesis": "join trend",
          "play_class": "join_trend",
          "side": "long",
          "entry": 1.353,
          "stop": 1.351,
          "target": 1.357,
          "confidence": "high",
          "citations": [],
          "notes": ""
        }
        """
        proposal = parse_proposal(raw)
        self.assertAlmostEqual(proposal.confidence, 0.8)


if __name__ == "__main__":
    unittest.main()
