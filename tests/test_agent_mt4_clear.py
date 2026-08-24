"""Unit tests for python -m agent.mt4_clear (no MT4 process)."""

from __future__ import annotations

import argparse
import io
import unittest
from unittest.mock import patch

from agent.mt4_clear import main, parse_prefix


class TestMt4ClearCli(unittest.TestCase):
    def test_prefix_must_be_sbox(self) -> None:
        self.assertEqual(parse_prefix("sbox."), "sbox.")
        self.assertEqual(parse_prefix("sbox.ticket.walk."), "sbox.ticket.walk.")
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_prefix("EURUSD")
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_prefix("sbox")

    def test_main_writes_clear_and_prints_json(self) -> None:
        payload = {"ok": True, "cmd_id": "abc", "prefix": "sbox."}
        buf = io.StringIO()
        with patch("agent.mt4_clear.mt4_bridge.clear_layer", return_value=payload) as clear:
            with patch("sys.stdout", buf):
                code = main([])
        self.assertEqual(code, 0)
        clear.assert_called_once_with(
            "sbox.", symbol="EUR_USD", timeframe="D"
        )
        self.assertIn("abc", buf.getvalue())

    def test_main_exits_nonzero_when_ea_down(self) -> None:
        payload = {"ok": False, "error": "EA heartbeat missing or stale"}
        with patch("agent.mt4_clear.mt4_bridge.clear_layer", return_value=payload):
            with patch("sys.stderr"):
                with patch("sys.stdout"):
                    code = main(["--quiet"])
        self.assertEqual(code, 1)

    def test_main_passes_prefix(self) -> None:
        payload = {"ok": True, "cmd_id": "x", "prefix": "sbox.regime."}
        with patch("agent.mt4_clear.mt4_bridge.clear_layer", return_value=payload) as clear:
            with patch("sys.stdout"):
                code = main(["--prefix", "sbox.regime."])
        self.assertEqual(code, 0)
        clear.assert_called_once_with(
            "sbox.regime.", symbol="EUR_USD", timeframe="D"
        )


if __name__ == "__main__":
    unittest.main()
