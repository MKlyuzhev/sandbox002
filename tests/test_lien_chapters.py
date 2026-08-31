"""Unit tests for chapter/engine aliases (no network)."""

from __future__ import annotations

import unittest

from agent.lien_chapters import (
    DEFERRED_CHAPTERS,
    entry_lien_error,
    resolve_engine,
)


class TestResolveEngine(unittest.TestCase):
    def test_chapter_16(self) -> None:
        self.assertEqual(resolve_engine(chapter=16, engine=None), "perfect_order")

    def test_chapter_13(self) -> None:
        self.assertEqual(resolve_engine(chapter=13, engine=None), "fader")

    def test_engine_only(self) -> None:
        self.assertEqual(resolve_engine(chapter=None, engine="dbb"), "dbb")

    def test_default_mtf(self) -> None:
        self.assertEqual(resolve_engine(chapter=None, engine=None), "mtf")

    def test_mismatch(self) -> None:
        with self.assertRaises(ValueError):
            resolve_engine(chapter=16, engine="dbb")

    def test_deferred_error(self) -> None:
        for ch in DEFERRED_CHAPTERS:
            msg = entry_lien_error(ch)
            self.assertIn("not encoded yet", msg)
            self.assertIn("13", msg)


if __name__ == "__main__":
    unittest.main()
