"""Causal one-position paper walk (no look-forward on the decision).

Fill at the decision bar close after a policy pass. Stop / target are checked
from the next bar onward. Window end marks still-open trades at last close.
No RAG, no LLM, no broker orders. ``agent.executor`` is not used.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from agent import levels as levels_mod
from agent import policy, propose as propose_mod
from agent.journal import Journal
from agent.schema import Goal, PaperTrade, RunRecord, SimFill
from app import indicators, regime as regime_mod, regime_walk, risk as risk_lib

ClassifyFn = Callable[[list[dict[str, Any]]], dict[str, Any]]


def check_exit(
    side: str, stop: float, target: float, bar: dict[str, Any]
) -> tuple[str, float] | None:
    """Return (exit_status, price) if this bar trades through stop or target.

    Stop wins if both levels trade in the same bar. Gaps through the stop
    still exit at the stop (v1).
    """
    high = float(bar["high"])
    low = float(bar["low"])
    if side == "long":
        if low <= stop:
            return "stop", stop
        if high >= target:
            return "target", target
    elif side == "short":
        if high >= stop:
            return "stop", stop
        if low <= target:
            return "target", target
    return None


def _goal_for_walk(goal: Goal) -> Goal:
    return goal.model_copy(
        update={"mode": "paper", "no_rag": True, "no_llm": True}
    )


def _maybe_enter(
    analysis: dict[str, Any],
    goal: Goal,
) -> tuple[Any, Any] | None:
    """Return (proposal, verdict) if paper policy would queue a fill."""
    proposal = propose_mod.skeleton_proposal(analysis)
    proposal = levels_mod.apply_geometry(proposal, analysis, goal.instrument)
    verdict = policy.evaluate(analysis, proposal, goal)
    if verdict.action != "pending_exec":
        return None
    if proposal is None or proposal.entry is None or proposal.stop is None:
        return None
    if proposal.target is None or proposal.side not in ("long", "short"):
        return None
    return proposal, verdict


def _close_trade(
    trade: PaperTrade,
    *,
    exit_index: int,
    exit_time: str,
    exit_price: float,
    exit_status: str,
    journal: Journal | None,
) -> PaperTrade:
    try:
        r_realized = round(
            risk_lib.r_multiple(trade.entry, trade.stop, exit_price), 4
        )
    except risk_lib.RiskError:
        r_realized = None
    closed = trade.model_copy(
        update={
            "exit_index": exit_index,
            "exit_time": exit_time,
            "exit_price": exit_price,
            "exit_status": exit_status,
            "r_realized": r_realized,
        }
    )
    if journal is not None:
        journal.record_exit(
            SimFill(
                run_id=trade.run_id,
                status="filled_sim",
                fill_price=trade.entry,
                ts=trade.entry_time,
                note=f"walk exit {exit_status}",
                exit_status=exit_status,  # type: ignore[arg-type]
                exit_price=exit_price,
                exit_ts=exit_time,
                r_realized=r_realized,
            )
        )
    return closed


def _journal_entry(
    journal: Journal | None,
    goal: Goal,
    analysis: dict[str, Any],
    proposal: Any,
    verdict: Any,
    run_id: str,
    bar_time: str,
) -> None:
    if journal is None:
        return
    if proposal is not None:
        proposal = proposal.model_copy(update={"at_time": bar_time})
    analysis = dict(analysis)
    analysis["last_time"] = bar_time
    record = RunRecord(
        run_id=run_id,
        ts=bar_time,
        mode="paper",
        instrument=goal.instrument,
        granularity=goal.granularity,
        action="pending_exec",
        goal=goal,
        regime=analysis,
        proposal=proposal,
        risk=verdict,
        citations=[],
        tool_trace=[],
        error=None,
    )
    journal.append_run(record, queue_fill=False)
    journal.record_fill(
        SimFill(
            run_id=run_id,
            status="filled_sim",
            fill_price=float(proposal.entry),
            ts=bar_time,
            note="walk fill at decision-bar close; no broker",
        )
    )


def walk_paper(
    bars: list[dict[str, Any]],
    goal: Goal,
    *,
    lookback: int = regime_walk.DEFAULT_LOOKBACK,
    start_index: int | None = None,
    journal: Journal | None = None,
    classify_fn: ClassifyFn | None = None,
) -> list[PaperTrade]:
    """Walk causal windows; at most one open paper ticket at a time.

    Window at index ``i`` is ``bars[: i + 1][-lookback:]``. Step is always 1.
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
    trades: list[PaperTrade] = []
    open_trade: PaperTrade | None = None
    last_i = len(series) - 1

    for i in range(start_index, len(series)):
        bar = series[i]
        bar_time = str(bar.get("time") or "")

        exited_here = False
        if open_trade is not None and i > open_trade.entry_index:
            hit = check_exit(
                open_trade.side, open_trade.stop, open_trade.target, bar
            )
            if hit is not None:
                status, price = hit
                open_trade = _close_trade(
                    open_trade,
                    exit_index=i,
                    exit_time=bar_time,
                    exit_price=price,
                    exit_status=status,
                    journal=journal,
                )
                trades.append(open_trade)
                open_trade = None
                exited_here = True

        if open_trade is None and not exited_here:
            window = series[: i + 1][-lookback:]
            analysis = classify(window)
            analysis.setdefault("instrument", goal.instrument)
            analysis.setdefault("granularity", goal.granularity)
            entered = _maybe_enter(analysis, goal)
            if entered is not None:
                proposal, verdict = entered
                run_id = uuid.uuid4().hex
                open_trade = PaperTrade(
                    run_id=run_id,
                    entry_index=i,
                    entry_time=bar_time,
                    side=proposal.side,
                    play_class=proposal.play_class,
                    entry=float(proposal.entry),
                    stop=float(proposal.stop),
                    target=float(proposal.target),
                    reasons=list(verdict.reasons),
                )
                _journal_entry(
                    journal, goal, analysis, proposal, verdict, run_id, bar_time
                )

    if open_trade is not None:
        last = series[last_i]
        open_trade = _close_trade(
            open_trade,
            exit_index=last_i,
            exit_time=str(last.get("time") or ""),
            exit_price=float(last["close"]),
            exit_status="window_end",
            journal=journal,
        )
        trades.append(open_trade)

    return trades
