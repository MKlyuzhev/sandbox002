"""Unit tests for walk_jobs dispatch (no network)."""

from __future__ import annotations

import asyncio
import unittest

from agent.lien_chapters import entry_lien_error
from agent.walk_jobs import WalkJobError, execute_walk, truncate_trades


class TestTruncateTrades(unittest.TestCase):
    def test_small_unchanged(self) -> None:
        rows = [{"i": n} for n in range(5)]
        out, truncated = truncate_trades(rows)
        self.assertFalse(truncated)
        self.assertEqual(out, rows)

    def test_long_head_and_tail(self) -> None:
        rows = [{"i": n} for n in range(25)]
        out, truncated = truncate_trades(rows)
        self.assertTrue(truncated)
        self.assertEqual(len(out), 20)
        self.assertEqual(out[0]["i"], 0)
        self.assertEqual(out[-1]["i"], 24)


class TestExecuteWalkValidation(unittest.TestCase):
    def test_bad_kind(self) -> None:
        with self.assertRaises(WalkJobError):
            asyncio.run(
                execute_walk(
                    "tester",
                    "EUR_USD",
                    "2024-01-01T00:00:00Z",
                    "2024-02-01T00:00:00Z",
                    no_journal=True,
                )
            )

    def test_missing_window(self) -> None:
        with self.assertRaises(WalkJobError):
            asyncio.run(
                execute_walk("ch7", "EUR_USD", "", "2024-02-01T00:00:00Z", no_journal=True)
            )

    def test_unencoded_chapter(self) -> None:
        with self.assertRaises(WalkJobError) as ctx:
            asyncio.run(
                execute_walk(
                    "lien",
                    "EUR_USD",
                    "2024-01-01T00:00:00Z",
                    "2024-02-01T00:00:00Z",
                    chapter=10,
                    no_journal=True,
                )
            )
        self.assertEqual(str(ctx.exception), entry_lien_error(10))

    def test_lien_requires_chapter(self) -> None:
        with self.assertRaises(WalkJobError):
            asyncio.run(
                execute_walk(
                    "lien",
                    "EUR_USD",
                    "2024-01-01T00:00:00Z",
                    "2024-02-01T00:00:00Z",
                    no_journal=True,
                )
            )


if __name__ == "__main__":
    unittest.main()
