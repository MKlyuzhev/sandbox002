"""Unit tests for Lien RAG fidelity catalog (no network, no Chroma)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from agent.fidelity import (
    CLAIMS,
    check_pin,
    check_static,
    fold,
    main,
    run_static,
)
from agent.lien_chapters import CHAPTER_TO_ENGINE, DEFERRED_CHAPTERS


class TestCatalog(unittest.TestCase):
    def test_static_passes(self) -> None:
        report = run_static()
        self.assertTrue(report.ok, [c.model_dump() for c in report.failed])

    def test_encoded_chapters_match_engine_map(self) -> None:
        encoded = {c.chapter: c.engine for c in CLAIMS if c.encoded}
        self.assertEqual(encoded[8], "mtf")
        self.assertEqual(encoded[9], "dbb")
        self.assertEqual(encoded[13], "fader")
        self.assertEqual(encoded[14], "breakout20")
        self.assertEqual(encoded[16], "perfect_order")
        for chapter, engine in CHAPTER_TO_ENGINE.items():
            self.assertEqual(encoded[chapter], engine)

    def test_deferred_match_lien_chapters(self) -> None:
        deferred = {c.chapter for c in CLAIMS if not c.encoded}
        self.assertEqual(deferred, set(DEFERRED_CHAPTERS))

    def test_encoded_have_mcp_tools(self) -> None:
        for claim in CLAIMS:
            if claim.encoded:
                self.assertTrue(claim.mcp_tool, claim.claim_id)

    def test_fold_ligature(self) -> None:
        self.assertIn("profit", fold("proﬁt potentials"))


class TestPin(unittest.TestCase):
    def test_must_contain_on_cited_text(self) -> None:
        def _chunk(source: str, idx: int) -> dict:
            texts = {
                87: "The fader strategy waits for a 15 pips probe.",
                88: "After the fade, look for a 20-day high.",
            }
            if idx not in texts:
                return {"error": "missing"}
            return {"source": source, "chunk_index": idx, "text": texts[idx]}

        with patch("agent.fidelity.get_source_chunk", side_effect=_chunk):
            checks = check_pin()
        fader = next(c for c in checks if c.claim_id == "ch13_fader")
        self.assertTrue(fader.ok, fader.detail)

    def test_missing_chunk_fails(self) -> None:
        with patch(
            "agent.fidelity.get_source_chunk",
            return_value={"error": "No chunk found"},
        ):
            checks = check_pin()
        self.assertTrue(any(not c.ok and c.kind == "pin" for c in checks))


class TestCli(unittest.TestCase):
    def test_static_cli_exit_zero(self) -> None:
        from io import StringIO

        with patch("sys.stdout", StringIO()):
            self.assertEqual(main([]), 0)

    def test_static_checks_cover_citations(self) -> None:
        kinds = {c.kind for c in check_static()}
        self.assertIn("citations", kinds)
        self.assertIn("registry", kinds)
        self.assertIn("unencoded", kinds)


if __name__ == "__main__":
    unittest.main()
