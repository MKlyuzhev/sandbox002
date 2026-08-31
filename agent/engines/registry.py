"""Engine registry: selection by regime and highest-confidence dispatch.

Priority order matters only as a tie-break; specialized chapter engines come
before the generic Ch. 7 fallback. Selection always honors the Ch. 7 gate
(``trend_waning`` -> no engines) and the regime's ``allowed_play_classes``.
"""

from __future__ import annotations

from typing import Any

from agent.engines.base import Engine, EngineContext, EngineResult
from agent.engines.breakout20 import Breakout20Engine
from agent.engines.ch7 import Ch7Engine
from agent.engines.dbb import DbbEngine
from agent.engines.fader import FaderEngine
from agent.engines.mtf import MtfEngine
from agent.engines.perfect_order import PerfectOrderEngine
from agent.schema import Goal

# Priority order (specialized first, generic Ch. 7 fallback last).
REGISTRY: list[Engine] = [
    MtfEngine(),
    DbbEngine(),
    FaderEngine(),
    Breakout20Engine(),
    PerfectOrderEngine(),
    Ch7Engine(),
]


def by_chapter() -> dict[int, Engine]:
    return {eng.chapter: eng for eng in REGISTRY}


def select(analysis: dict[str, Any], goal: Goal) -> list[Engine]:
    """Engines allowed for this regime, in registry priority order.

    Empty if the trend is waning (Ch. 7 gate wins). ``goal.engines`` optionally
    restricts to a chapter allow-list.
    """
    if analysis.get("trend_waning"):
        return []
    allowed = set(analysis.get("allowed_play_classes") or [])
    wanted = set(goal.engines) if goal.engines else None
    out: list[Engine] = []
    for eng in REGISTRY:
        if wanted is not None and eng.chapter not in wanted:
            continue
        if allowed & set(eng.play_classes):
            out.append(eng)
    return out


def required_timeframes(engines: list[Engine], goal: Goal) -> set[str]:
    """Union of timeframes the selected engines need for this goal."""
    tfs: set[str] = {goal.granularity}
    for eng in engines:
        tfs.update(eng.granularities(goal))
    return tfs


def run_and_pick(
    engines: list[Engine],
    ctx: EngineContext,
) -> tuple[EngineResult | None, list[EngineResult]]:
    """Run all engines; pick the highest-confidence firing result.

    Ties break by registry priority (engines earlier in ``engines`` win).
    Returns ``(chosen, all_results)``; ``chosen`` is None when nothing fired.
    """
    results = [eng.signal(ctx) for eng in engines]
    firing = [r for r in results if r.firing]
    if not firing:
        return None, results
    # ``engines`` order is the priority order; a stable max keeps the earliest
    # engine on ties.
    chosen = max(firing, key=lambda r: r.confidence)
    best = chosen.confidence
    for r in firing:
        if r.confidence == best:
            chosen = r
            break
    return chosen, results
