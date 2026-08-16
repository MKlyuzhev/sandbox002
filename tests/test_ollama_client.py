"""Unit tests for Ollama load-status helpers (no network)."""

from __future__ import annotations

import unittest

from app.ollama_client import model_is_loaded


class TestModelIsLoaded(unittest.TestCase):
    def test_exact_and_prefix(self) -> None:
        running = ["qwen3:4b", "nomic-embed-text:latest"]
        self.assertTrue(model_is_loaded("qwen3:4b", running))
        self.assertTrue(model_is_loaded("nomic-embed-text", running))
        self.assertFalse(model_is_loaded("moondream", running))
        self.assertFalse(model_is_loaded("", running))


if __name__ == "__main__":
    unittest.main()
