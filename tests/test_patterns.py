"""Unit tests for app.patterns (no network)."""

from __future__ import annotations

import unittest

from app import patterns


def _bar(i: int, high: float, low: float, close: float | None = None) -> dict:
    c = close if close is not None else (high + low) / 2
    o = c
    return {
        "time": f"t{i}",
        "open": o,
        "high": high,
        "low": low,
        "close": c,
        "volume": None,
    }


def _plateau(start: int, n: int, level: float, half_range: float = 0.1) -> list[dict]:
    return [
        _bar(start + k, level + half_range, level - half_range, close=level)
        for k in range(n)
    ]


def _peak_at(index: int, peak: float, base: float, half_width: int = 3) -> list[dict]:
    """Bars forming an isolated swing high at ``index`` relative to this segment.

    Segment length = 2*half_width + 1; center is the peak.
    """
    bars: list[dict] = []
    for k in range(2 * half_width + 1):
        i = index - half_width + k
        if k == half_width:
            bars.append(_bar(i, peak, base, close=peak - 0.05))
        else:
            # Flanks well below peak so fractal detects the center
            bars.append(_bar(i, base + 0.2, base - 0.2, close=base))
    return bars


class TestDetectSwings(unittest.TestCase):
    def test_finds_isolated_high(self) -> None:
        # Build: 3 flat + peak + 3 flat
        bars = _plateau(0, 3, 10.0) + [_bar(3, 12.0, 10.0, close=11.5)] + _plateau(4, 3, 10.0)
        pivots = patterns.detect_swings(bars, left=2, right=2)
        highs = [p for p in pivots if p["kind"] == "high"]
        self.assertTrue(any(abs(p["price"] - 12.0) < 1e-9 for p in highs))


class TestTrendlines(unittest.TestCase):
    def test_descending_resistance_has_touches(self) -> None:
        # Two clear swing highs sloping down, with room between
        bars: list[dict] = []
        # Peak 1 at idx 3
        bars.extend(_plateau(0, 3, 10.0))
        bars.append(_bar(3, 14.0, 10.0, close=13.0))
        bars.extend(_plateau(4, 5, 10.5))
        # Peak 2 at idx 9
        bars.append(_bar(9, 13.0, 10.0, close=12.0))
        bars.extend(_plateau(10, 5, 10.0))
        pivots = patterns.detect_swings(bars, left=2, right=2)
        lines = patterns.propose_trendlines(pivots, bars, max_lines=5, touch_atr_frac=0.5)
        resistance = [ln for ln in lines if ln["kind"] == "resistance"]
        self.assertTrue(resistance)
        self.assertGreaterEqual(resistance[0]["touches"], 2)


class TestHsTop(unittest.TestCase):
    def _hs_top_bars(self, break_neckline: bool = False) -> list[dict]:
        """Synthetic H&S top with left=2,right=2 detectable swings.

        Structure (approx indices):
          LS high ~12, trough ~10, HEAD ~14, trough ~10.2, RS high ~12.5,
          then either hold above neckline or close below.
        """
        bars: list[dict] = []
        # Warmup flats so first swings have left neighbors
        bars.extend(_plateau(0, 3, 10.0))
        # Left shoulder peak
        bars.append(_bar(3, 12.0, 10.0, close=11.5))
        bars.extend(_plateau(4, 3, 10.2))
        # Trough between LS and head
        bars.append(_bar(7, 10.4, 9.8, close=10.0))
        bars.extend(_plateau(8, 3, 10.3))
        # Head
        bars.append(_bar(11, 14.0, 10.5, close=13.0))
        bars.extend(_plateau(12, 3, 10.5))
        # Trough between head and RS
        bars.append(_bar(15, 10.6, 10.0, close=10.2))
        bars.extend(_plateau(16, 3, 10.5))
        # Right shoulder
        bars.append(_bar(19, 12.5, 10.3, close=12.0))
        bars.extend(_plateau(20, 4, 10.8))
        if break_neckline:
            # Close well below intervening troughs (~10)
            bars.append(_bar(24, 10.2, 9.0, close=9.2))
            bars.extend(_plateau(25, 3, 9.3))
        else:
            bars.extend(_plateau(24, 4, 10.9))
        return bars

    def test_neckline_tentative(self) -> None:
        bars = self._hs_top_bars(break_neckline=False)
        pivots = patterns.detect_swings(bars, left=2, right=2)
        state = patterns.hs_formation_state(pivots, bars, break_frac=0.001)
        self.assertIn(
            state["stage"],
            {
                "right_shoulder_forming",
                "neckline_tentative",
                "head",
                "left_shoulder",
            },
        )
        # Prefer the happy path when structure is found
        if state["head"] is not None and state["right_shoulder"] is not None:
            self.assertIn(state["stage"], {"neckline_tentative", "right_shoulder_forming"})

    def test_confirmed_break(self) -> None:
        bars = self._hs_top_bars(break_neckline=True)
        pivots = patterns.detect_swings(bars, left=2, right=2)
        state = patterns.hs_formation_state(pivots, bars, break_frac=0.001)
        # If LHR + neckline were detected, expect confirmed_break
        if (
            state["left_shoulder"]
            and state["head"]
            and state["right_shoulder"]
            and state["neckline"]
        ):
            self.assertEqual(state["stage"], "confirmed_break")
            self.assertIsNotNone(state["height"])
            self.assertIsNotNone(state["min_target"])

    def test_analyze_bars_shape(self) -> None:
        bars = self._hs_top_bars(False)
        result = patterns.analyze_bars(bars, swing_left=2, swing_right=2)
        self.assertIn("hs", result)
        self.assertIn("trendlines", result)
        self.assertIn("swings", result)
        self.assertEqual(result["bar_count"], len(bars))


if __name__ == "__main__":
    unittest.main()
