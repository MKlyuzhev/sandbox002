"""Unit tests for app.formation_plot (no network, Agg backend)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import formation_plot, patterns
from tests.test_patterns import TestHsTop


class TestFormationPlot(unittest.TestCase):
    def test_writes_png_from_synthetic_hs(self) -> None:
        bars = TestHsTop()._hs_top_bars(break_neckline=True)
        analysis = patterns.analyze_bars(bars, swing_left=2, swing_right=2)
        analysis["instrument"] = "TEST"
        analysis["granularity"] = "H1"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "formation.png"
            saved = formation_plot.plot_formation(bars, analysis, path)
            self.assertTrue(saved.is_file())
            self.assertGreater(saved.stat().st_size, 0)

    def test_empty_bars_raises(self) -> None:
        with self.assertRaises(formation_plot.PlotError):
            formation_plot.plot_formation([], {}, "unused.png")


if __name__ == "__main__":
    unittest.main()
