"""Planner MCP tool signatures (no network)."""

from __future__ import annotations

import inspect
import unittest

from app import oanda_mcp


class TestPlannerMcpSignatures(unittest.TestCase):
    def test_scan_regimes(self) -> None:
        params = inspect.signature(oanda_mcp.scan_regimes).parameters
        self.assertIn("instruments", params)
        self.assertIn("drop_waning", params)
        self.assertIn("play_class", params)

    def test_run_graph_defaults(self) -> None:
        params = inspect.signature(oanda_mcp.run_graph).parameters
        self.assertEqual(params["no_llm"].default, True)
        self.assertEqual(params["mode"].default, "signal")
        self.assertEqual(params["mt4"].default, False)

    def test_run_walk(self) -> None:
        params = inspect.signature(oanda_mcp.run_walk).parameters
        self.assertIn("kind", params)
        self.assertIn("from_time", params)
        self.assertIn("chapter", params)


if __name__ == "__main__":
    unittest.main()
