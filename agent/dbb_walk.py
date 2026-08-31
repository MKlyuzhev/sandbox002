"""Causal Ch.9 Double Bollinger decision feed (wrapper around event_walk)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent.engines import dbb as dbb_mod
from agent.event_walk import event_decisions
from agent.schema import Goal
from app import regime_walk

ClassifyFn = Callable[[list[dict[str, Any]]], dict[str, Any]]


def dbb_decisions(
    bars: list[dict[str, Any]],
    goal: Goal,
    *,
    lookback: int = regime_walk.DEFAULT_LOOKBACK,
    start_index: int | None = None,
    classify_fn: ClassifyFn | None = None,
    buffer_pips: int = dbb_mod.BUFFER_PIPS,
) -> list[dict[str, Any]]:
    """Emit one Ch.9 decision per qualifying 1sigma-cross bar (policy-gated)."""
    return event_decisions(
        bars,
        goal,
        "dbb",
        lookback=lookback,
        start_index=start_index,
        classify_fn=classify_fn,
        buffer_pips=buffer_pips,
    )
