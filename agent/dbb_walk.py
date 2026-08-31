"""Causal Ch.9 Double Bollinger decision feed for the MT4 Strategy Tester.

Unlike the Ch.8 MTF rollover-peak logic, the Ch.9 signal is a discrete one-shot
event: a close crossing the 1sigma band (join out, or fade back in) is fully
knowable at that bar's close because the trigger already depends on the prior
bars' zones. So each qualifying bar emits one decision stamped with its own
time; the EA fills at the next bar's open and owns position state. Research
only; no broker orders.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent import policy
from agent.engines import dbb as dbb_mod
from agent.paper_walk import _goal_for_walk
from agent.schema import Citation, Goal, Proposal
from app import indicators, regime as regime_mod, regime_walk

ClassifyFn = Callable[[list[dict[str, Any]]], dict[str, Any]]


def _proposal_from_signal(out: dict[str, Any], ticket: dict[str, Any], at_time: str) -> Proposal:
    citations = [
        Citation(source=str(c["source"]), chunk_index=int(c["chunk_index"]))
        for c in out.get("citations", [])
    ]
    return Proposal(
        thesis=out.get("reason", ""),
        play_class=out.get("play_class", "breakout_watch"),
        side=out["signal"],
        entry=float(ticket["entry"]),
        stop=float(ticket["stop"]),
        target=float(ticket["target"]),
        at_time=at_time,
        engine="dbb",
        chapter=dbb_mod.CHAPTER,
        citations=citations,
        notes=out.get("note", ""),
    )


def _decision(out: dict[str, Any], ticket: dict[str, Any], signal_time: str) -> dict[str, Any]:
    return {
        "signal_time": signal_time,
        "side": out["signal"],
        "entry": float(ticket["entry"]),
        "stop": float(ticket["stop"]),
        "target": float(ticket["target"]),
        "play_class": out.get("play_class"),
    }


def dbb_decisions(
    bars: list[dict[str, Any]],
    goal: Goal,
    *,
    lookback: int = regime_walk.DEFAULT_LOOKBACK,
    start_index: int | None = None,
    classify_fn: ClassifyFn | None = None,
    buffer_pips: int = dbb_mod.BUFFER_PIPS,
) -> list[dict[str, Any]]:
    """Emit one Ch.9 decision per qualifying 1sigma-cross bar (policy-gated).

    Causal: bar ``i`` is classified from ``bars[:i+1][-lookback:]`` only. A
    decision is emitted when ``dbb_signal`` fires and ``policy.evaluate`` passes
    (regime allows the play class, planned R >= 2). Each decision is stamped with
    the signal bar's own time; the tester fills at the next bar's open.
    """
    if lookback < indicators.MIN_BARS:
        raise regime_walk.WalkError(
            f"lookback must be >= {indicators.MIN_BARS}; got {lookback}"
        )

    series = regime_walk.drop_incomplete(bars)

    if start_index is None:
        start_index = lookback - 1
    if start_index < lookback - 1:
        raise regime_walk.WalkError(
            f"start_index {start_index} needs {lookback} bars of history "
            f"(warmup before the test start)"
        )
    if start_index >= len(series):
        raise regime_walk.WalkError("start_index is past the last complete bar")

    goal = _goal_for_walk(goal)
    classify = classify_fn or regime_mod.analyze_bars

    decisions: list[dict[str, Any]] = []
    for i in range(start_index, len(series)):
        window = series[: i + 1][-lookback:]
        analysis = dict(classify(window))
        analysis.setdefault("instrument", goal.instrument)
        analysis.setdefault("granularity", goal.granularity)

        out = dbb_mod.dbb_signal(
            analysis,
            goal.instrument,
            buffer_pips=buffer_pips,
            granularity=goal.granularity,
        )
        ticket = out.get("ticket")
        if out["signal"] not in ("long", "short") or not ticket:
            continue

        signal_time = str(series[i].get("time") or "")
        proposal = _proposal_from_signal(out, ticket, signal_time)
        verdict = policy.evaluate(analysis, proposal, goal)
        if not verdict.ok:
            continue
        decisions.append(_decision(out, ticket, signal_time))

    return decisions
