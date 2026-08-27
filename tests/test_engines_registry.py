"""Unit tests for the engine registry: selection and confidence dispatch."""

from __future__ import annotations

import unittest

from agent.engines import registry
from agent.engines.base import EngineContext, EngineResult
from agent.engines.ch7 import Ch7Engine
from agent.engines.mtf import MtfEngine
from agent.schema import Goal, PlayClass


def _analysis(regime: str, plays: list[str], *, waning: bool = False) -> dict:
    return {
        "regime": regime,
        "direction": "up",
        "trend_waning": waning,
        "allowed_play_classes": plays,
        "confidence": 0.6,
    }


class _StubEngine:
    def __init__(self, chapter: int, result: EngineResult) -> None:
        self.chapter = chapter
        self.name = f"stub{chapter}"
        self.play_classes: tuple[PlayClass, ...] = ("join_trend",)
        self._result = result

    def granularities(self, goal: Goal) -> tuple[str, ...]:
        return (goal.granularity,)

    def signal(self, ctx: EngineContext) -> EngineResult:
        return self._result


def _fire(chapter: int, conf: float) -> EngineResult:
    return EngineResult(
        engine=f"stub{chapter}",
        chapter=chapter,
        signal="long",
        play_class="join_trend",
        ticket={"side": "long", "entry": 1.1, "stop": 1.09, "target": 1.12},
        confidence=conf,
    )


class TestSelect(unittest.TestCase):
    def test_waning_selects_nothing(self) -> None:
        analysis = _analysis("trend", ["join_trend"], waning=True)
        self.assertEqual(registry.select(analysis, Goal()), [])

    def test_trend_selects_mtf_and_ch7(self) -> None:
        analysis = _analysis("trend", ["join_trend"])
        chapters = [e.chapter for e in registry.select(analysis, Goal())]
        self.assertIn(8, chapters)
        self.assertIn(7, chapters)
        # MTF (specialized) comes before the Ch.7 fallback.
        self.assertLess(chapters.index(8), chapters.index(7))

    def test_range_excludes_mtf(self) -> None:
        analysis = _analysis("range", ["fade_range"])
        chapters = [e.chapter for e in registry.select(analysis, Goal())]
        self.assertEqual(chapters, [7])

    def test_goal_engines_allow_list(self) -> None:
        analysis = _analysis("trend", ["join_trend"])
        chapters = [e.chapter for e in registry.select(analysis, Goal(engines=[7]))]
        self.assertEqual(chapters, [7])


class TestRequiredTimeframes(unittest.TestCase):
    def test_union_includes_ltf(self) -> None:
        goal = Goal(granularity="D", ltf_granularity="H1")
        engines = [MtfEngine(), Ch7Engine()]
        self.assertEqual(registry.required_timeframes(engines, goal), {"D", "H1"})


class TestRunAndPick(unittest.TestCase):
    def _ctx(self) -> EngineContext:
        return EngineContext(instrument="EUR_USD", goal=Goal(), analyses={})

    def test_highest_confidence_wins(self) -> None:
        engines = [_StubEngine(8, _fire(8, 0.4)), _StubEngine(9, _fire(9, 0.9))]
        chosen, results = registry.run_and_pick(engines, self._ctx())
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual(chosen.chapter, 9)
        self.assertEqual(len(results), 2)

    def test_tie_breaks_by_priority(self) -> None:
        engines = [_StubEngine(8, _fire(8, 0.5)), _StubEngine(9, _fire(9, 0.5))]
        chosen, _ = registry.run_and_pick(engines, self._ctx())
        assert chosen is not None
        self.assertEqual(chosen.chapter, 8)

    def test_none_firing_returns_none(self) -> None:
        dead = EngineResult(engine="stub8", chapter=8, signal="none")
        chosen, results = registry.run_and_pick(
            [_StubEngine(8, dead)], self._ctx()
        )
        self.assertIsNone(chosen)
        self.assertEqual(len(results), 1)


if __name__ == "__main__":
    unittest.main()
