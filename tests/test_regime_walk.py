"""Unit tests for causal regime walk (no network)."""

from __future__ import annotations

import copy
import unittest
from datetime import datetime, timedelta, timezone

from app import mt4_bridge, regime_walk
from tests.test_indicators import _range_bars, _trend_bars


def _stamp(bars: list[dict], start: datetime | None = None) -> list[dict]:
    origin = start or datetime(2024, 1, 1, tzinfo=timezone.utc)
    out: list[dict] = []
    for i, b in enumerate(bars):
        nb = dict(b)
        ts = origin + timedelta(days=i)
        nb["time"] = ts.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
        nb["complete"] = True
        out.append(nb)
    return out


class TestPrepareBars(unittest.TestCase):
    def test_drops_incomplete_last_bar(self) -> None:
        bars = _stamp(_range_bars(40))
        bars[-1]["complete"] = False
        cleaned = regime_walk.prepare_bars(bars)
        self.assertEqual(len(cleaned), 39)
        self.assertTrue(all(b.get("complete", True) for b in cleaned))

    def test_drops_bars_after_to_time(self) -> None:
        bars = _stamp(_range_bars(10))
        cut = bars[4]["time"]
        trimmed = regime_walk.drop_after(bars, cut)
        self.assertEqual(len(trimmed), 5)
        self.assertEqual(trimmed[-1]["time"], cut)


class TestWalkCausal(unittest.TestCase):
    def test_range_then_trend_labels(self) -> None:
        raw = _range_bars(80, amp=0.0003) + _trend_bars(80, start=1.2, step=0.01)
        bars = _stamp(raw)
        lookback = 40
        result = regime_walk.walk_and_collapse(
            bars, lookback=lookback, step=1, start_index=lookback - 1
        )
        early = [s for s in result["steps"] if s["index"] < 80]
        late = [s for s in result["steps"] if s["index"] >= 120]
        self.assertTrue(early)
        self.assertTrue(late)
        self.assertTrue(all(s["regime"] == "range" for s in early))
        self.assertTrue(any(s["regime"] == "trend" for s in late))
        self.assertGreaterEqual(result["summary"]["run_count"], 2)

    def test_mutating_future_bar_does_not_change_earlier_step(self) -> None:
        raw = _range_bars(50, amp=0.0003) + _trend_bars(50, start=1.2, step=0.01)
        bars = _stamp(raw)
        lookback = 40
        start = lookback - 1
        first = regime_walk.walk(bars, lookback=lookback, step=1, start_index=start)
        mid = first[5]
        i = mid["index"]
        self.assertLess(i + 1, len(bars))
        mutated = copy.deepcopy(bars)
        mutated[i + 1]["high"] = 99.0
        mutated[i + 1]["low"] = 0.01
        mutated[i + 1]["close"] = 50.0
        mutated[i + 1]["open"] = 50.0
        second = regime_walk.walk(
            mutated, lookback=lookback, step=1, start_index=start
        )
        a = next(s for s in first if s["index"] == i)
        b = next(s for s in second if s["index"] == i)
        self.assertEqual(a["regime"], b["regime"])
        self.assertEqual(a["direction"], b["direction"])
        self.assertEqual(a["trend_waning"], b["trend_waning"])
        self.assertEqual(a["confidence"], b["confidence"])

    def test_incomplete_last_bar_is_ignored(self) -> None:
        bars = _stamp(_range_bars(50))
        lookback = 40
        baseline = regime_walk.walk(
            bars, lookback=lookback, step=1, start_index=lookback - 1
        )
        extra = dict(bars[-1])
        extra["complete"] = False
        extra["close"] = 9.99
        extra["time"] = "2025-12-31T00:00:00.000000000Z"
        with_incomplete = bars + [extra]
        walked = regime_walk.walk(
            with_incomplete, lookback=lookback, step=1, start_index=lookback - 1
        )
        self.assertEqual(len(walked), len(baseline))
        self.assertEqual(walked[-1]["time"], baseline[-1]["time"])
        self.assertNotEqual(walked[-1]["close"], 9.99)

    def test_start_index_requires_warmup(self) -> None:
        bars = _stamp(_range_bars(40))
        with self.assertRaises(regime_walk.WalkError):
            regime_walk.walk(bars, lookback=40, start_index=10)

    def test_window_is_suffix_of_prefix(self) -> None:
        """Sanity: walk never receives a longer series than i+1."""
        seen: list[int] = []
        original = regime_walk.regime.analyze_bars

        def _spy(window: list[dict]) -> dict:
            seen.append(len(window))
            return original(window)

        bars = _stamp(_range_bars(45))
        lookback = 40
        try:
            regime_walk.regime.analyze_bars = _spy  # type: ignore[method-assign]
            regime_walk.walk(bars, lookback=lookback, start_index=lookback - 1)
        finally:
            regime_walk.regime.analyze_bars = original  # type: ignore[method-assign]
        self.assertTrue(seen)
        self.assertTrue(all(n == lookback for n in seen))


class TestCollapseAndObjects(unittest.TestCase):
    def test_collapse_high_low_from_run_steps_only(self) -> None:
        steps = [
            {
                "index": 0,
                "time": "t0",
                "high": 1.2,
                "low": 1.1,
                "regime": "range",
                "direction": None,
                "trend_waning": False,
            },
            {
                "index": 1,
                "time": "t1",
                "high": 1.25,
                "low": 1.05,
                "regime": "range",
                "direction": None,
                "trend_waning": False,
            },
            {
                "index": 2,
                "time": "t2",
                "high": 2.0,
                "low": 1.9,
                "regime": "trend",
                "direction": "up",
                "trend_waning": False,
            },
        ]
        runs = regime_walk.collapse_runs(steps)
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0]["high"], 1.25)
        self.assertEqual(runs[0]["low"], 1.05)
        self.assertEqual(runs[1]["high"], 2.0)

    def test_objects_use_walk_prefix(self) -> None:
        raw = _range_bars(50, amp=0.0003) + _trend_bars(40, start=1.2, step=0.01)
        bars = _stamp(raw)
        result = regime_walk.walk_and_collapse(
            bars, lookback=40, step=1, start_index=39
        )
        objects = mt4_bridge.regime_walk_to_objects(result, offset_seconds=0)
        self.assertTrue(objects)
        self.assertTrue(all(o["name"].startswith("sbox.regime.walk.") for o in objects))
        types = {o["type"] for o in objects}
        self.assertIn("rectangle", types)
        self.assertIn("text", types)
        self.assertIn("label", types)
        self.assertNotEqual(mt4_bridge.REGIME_WALK_PREFIX, mt4_bridge.REGIME_PREFIX)
        label = next(o for o in objects if o["type"] == "label")
        self.assertIn("p_hat=", label["text"])
        self.assertIn("brier=", label["text"])
        self.assertIn("show=both", label["text"])

    def test_mt4_show_selects_ranges_or_markers(self) -> None:
        raw = _range_bars(50, amp=0.0003) + _trend_bars(40, start=1.2, step=0.01)
        bars = _stamp(raw)
        result = regime_walk.walk_and_collapse(
            bars, lookback=40, step=1, start_index=39
        )
        ranges = mt4_bridge.regime_walk_to_objects(
            result, offset_seconds=0, show="ranges"
        )
        markers = mt4_bridge.regime_walk_to_objects(
            result, offset_seconds=0, show="markers"
        )
        both = mt4_bridge.regime_walk_to_objects(
            result, offset_seconds=0, show="both"
        )
        self.assertTrue(any(o["type"] == "rectangle" for o in ranges))
        self.assertFalse(any(".watch." in o["name"] for o in ranges))
        self.assertFalse(any(o["type"] == "rectangle" for o in markers))
        self.assertTrue(any(o["type"] == "label" for o in ranges))
        self.assertTrue(any(o["type"] == "label" for o in markers))
        self.assertTrue(any(o["type"] == "rectangle" for o in both))
        with self.assertRaises(mt4_bridge.Mt4BridgeError):
            mt4_bridge.regime_walk_to_objects(result, offset_seconds=0, show="nope")


def _fake_step(
    index: int,
    regime: str,
    *,
    direction: str | None = None,
    trend_waning: bool = False,
    adx: float = 18.0,
    confidence: float = 0.7,
    ma_perfect_order: str | None = None,
    trend_x: int = 1,
    range_x: int = 4,
    high: float = 1.2,
) -> dict:
    return {
        "index": index,
        "time": f"2024-01-{(index % 28) + 1:02d}T00:00:00.000000000Z",
        "high": high,
        "low": high - 0.01,
        "regime": regime,
        "direction": direction,
        "trend_waning": trend_waning,
        "confidence": confidence,
        "ma_perfect_order": ma_perfect_order,
        "trend_x_count": trend_x,
        "range_x_count": range_x,
        "adx": {"adx": adx, "slope": 0.0, "rising": False},
    }


class TestChangeProbCausal(unittest.TestCase):
    def test_mutating_future_bar_does_not_change_p_hat_or_instability(self) -> None:
        raw = _range_bars(50, amp=0.0003) + _trend_bars(50, start=1.2, step=0.01)
        bars = _stamp(raw)
        lookback = 40
        start = lookback - 1
        first = regime_walk.walk_and_collapse(
            bars, lookback=lookback, step=1, start_index=start, horizon=5, min_n=10
        )
        mid = first["steps"][5]
        i = mid["index"]
        self.assertLess(i + 1, len(bars))
        mutated = copy.deepcopy(bars)
        mutated[i + 1]["high"] = 99.0
        mutated[i + 1]["low"] = 0.01
        mutated[i + 1]["close"] = 50.0
        mutated[i + 1]["open"] = 50.0
        second = regime_walk.walk_and_collapse(
            mutated, lookback=lookback, step=1, start_index=start, horizon=5, min_n=10
        )
        a = next(s for s in first["steps"] if s["index"] == i)
        b = next(s for s in second["steps"] if s["index"] == i)
        self.assertEqual(a["instability"], b["instability"])
        self.assertEqual(a["p_hat"], b["p_hat"])
        self.assertEqual(a["n_hist"], b["n_hist"])
        self.assertEqual(a["bucket"], b["bucket"])

    def test_p_hat_ignores_episodes_whose_outcome_is_not_yet_known(self) -> None:
        """At i, only j with j+h <= i count. A later flip must not leak into p_hat."""
        horizon = 5
        min_n = 5
        steps = [_fake_step(i, "range") for i in range(14)]
        steps.append(_fake_step(14, "trend", direction="up", adx=40.0, trend_x=4, range_x=1))
        steps.extend(_fake_step(i, "range") for i in range(15, 17))
        regime_walk.attach_change_prob(steps, horizon=horizon, min_n=min_n)
        # At i=12, eligible j are 0..7. Outcomes 5..12 are still range → 0 changes.
        # j=9 needs bar 14 (trend), which is after i=12 and must be ignored.
        at_12 = steps[12]
        self.assertGreaterEqual(at_12["n_hist"], min_n)
        self.assertEqual(at_12["p_hat"], 0.0)
        self.assertIsNone(at_12["p_hat_note"])
        # At i=15 (range again), j=9 is eligible (9+5==14) and that episode changed.
        at_15 = steps[15]
        self.assertGreater(at_15["n_hist"], 0)
        self.assertIsNotNone(at_15["p_hat"])
        self.assertGreater(at_15["p_hat"], 0.0)

    def test_delayed_eval_matches_change_from_i_minus_h_to_i(self) -> None:
        horizon = 5
        steps = [_fake_step(i, "range") for i in range(10)]
        steps[9] = _fake_step(9, "trend", direction="up", adx=42.0, trend_x=5, range_x=0)
        regime_walk.attach_change_prob(steps, horizon=horizon, min_n=3)
        scored = steps[9]
        origin = steps[9 - horizon]
        self.assertTrue(scored["eval_changed"])
        self.assertEqual(scored["eval_changed"], regime_walk.regime_changed(origin, scored))
        self.assertEqual(scored["eval_p_hat"], origin["p_hat"])
        if scored["eval_p_hat"] is not None:
            y = 1.0
            self.assertAlmostEqual(
                scored["eval_brier"], (float(scored["eval_p_hat"]) - y) ** 2, places=4
            )
        unchanged = steps[7]
        self.assertFalse(unchanged["eval_changed"])
        self.assertEqual(
            unchanged["eval_changed"],
            regime_walk.regime_changed(steps[7 - horizon], unchanged),
        )

    def test_instability_and_p_hat_rise_near_range_to_trend_without_lookahead(self) -> None:
        raw = (
            _range_bars(70, amp=0.0003)
            + _trend_bars(50, start=1.2, step=0.01)
            + _range_bars(70, level=1.7, amp=0.0003)
        )
        bars = _stamp(raw)
        lookback = 40
        result = regime_walk.walk_and_collapse(
            bars, lookback=lookback, step=1, start_index=lookback - 1, horizon=5, min_n=8
        )
        steps = result["steps"]
        first_non_range = next(s for s in steps if s["regime"] != "range")
        boundary = [
            s
            for s in steps
            if first_non_range["index"] - 2 <= s["index"] <= first_non_range["index"]
        ]
        locked_trend = [
            s
            for s in steps
            if first_non_range["index"] + 8 <= s["index"] <= first_non_range["index"] + 20
            and s["regime"] == "trend"
        ]
        self.assertTrue(boundary)
        self.assertTrue(locked_trend)
        # Range X-counts stay tight, so the join is the ADX-boundary / flip
        # window — elevated vs the later locked trend (ADX >> 30, wide X-gap).
        self.assertGreater(
            max(s["instability"] for s in boundary),
            sum(s["instability"] for s in locked_trend) / len(locked_trend),
        )

        early_p = [
            s["p_hat"]
            for s in steps
            if s["index"] < 55 and s["p_hat"] is not None
        ]
        late_range = [
            s
            for s in steps
            if s["index"] >= 140 and s["regime"] == "range" and s["p_hat"] is not None
        ]
        self.assertTrue(early_p)
        self.assertTrue(late_range)
        self.assertGreater(
            sum(s["p_hat"] for s in late_range) / len(late_range),
            sum(early_p) / len(early_p),
        )

        scored = boundary[-1]
        i = scored["index"]
        mutated = copy.deepcopy(bars)
        mutated[i + 1]["high"] = 99.0
        mutated[i + 1]["close"] = 50.0
        mutated[i + 1]["open"] = 50.0
        again = regime_walk.walk_and_collapse(
            mutated, lookback=lookback, step=1, start_index=lookback - 1, horizon=5, min_n=8
        )
        twin = next(s for s in again["steps"] if s["index"] == i)
        self.assertEqual(scored["instability"], twin["instability"])
        self.assertEqual(scored["p_hat"], twin["p_hat"])

    def test_watch_objects_only_when_thresholds_hit(self) -> None:
        steps = [_fake_step(i, "range") for i in range(3)]
        steps[0]["instability"] = 0.1
        steps[0]["p_hat"] = 0.1
        steps[1]["instability"] = 0.7
        steps[1]["p_hat"] = None
        steps[2]["instability"] = 0.2
        steps[2]["p_hat"] = 0.6
        result = {
            "steps": steps,
            "runs": [],
            "summary": {
                "step_count": 3,
                "run_count": 0,
                "regime_counts": {"range": 3},
                "last_p_hat": 0.6,
                "brier": 0.1,
            },
        }
        objects = mt4_bridge.regime_walk_to_objects(
            result, offset_seconds=0, phat_watch=0.5, instability_watch=0.6
        )
        watches = [o for o in objects if ".watch." in o["name"] and o["type"] == "arrow"]
        self.assertEqual(len(watches), 2)
        ranges_only = mt4_bridge.regime_walk_to_objects(
            result, offset_seconds=0, show="ranges"
        )
        self.assertFalse(any(".watch." in o["name"] for o in ranges_only))


if __name__ == "__main__":
    unittest.main()
