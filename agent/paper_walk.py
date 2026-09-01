"""Causal one-position paper walk (no look-forward on the decision).

``--fill close`` (default): fill at the decision bar close after a policy pass.
Stop / target are checked from the next bar onward. Window end marks still-open
trades at last close.

``--fill rest``: engines still run on mids; fill waits for the next bar's
taking-side open (long ask / short bid). Exits use the making side. Research
only. No RAG, no LLM, no broker orders. ``agent.executor`` is not used.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from typing import Any

from agent import levels as levels_mod
from agent import policy, propose as propose_mod
from agent.journal import Journal
from agent.schema import Goal, PaperTrade, RunRecord, SimFill, WalkEquity, WalkResult
from agent.walk_exec import (
    RestPending,
    check_exit_rest,
    fill_mode_of,
    may_check_exit,
    realize_rest_trade,
    rest_journal_note,
    rest_pnl,
    window_end_price,
)
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


def summarize_equity(
    walk_id: str,
    trades: Sequence[Any],
    starting_equity: float,
    risk_fraction: float,
) -> WalkEquity:
    """Walk-level simulated equity stats from sequential compounded fills."""
    rs = [float(t.r_realized) for t in trades if t.r_realized is not None]
    wins = sum(1 for r in rs if r > 0)
    losses = sum(1 for r in rs if r < 0)
    scratches = sum(1 for r in rs if r == 0)
    n = len(rs)
    ending = starting_equity
    peak = starting_equity
    max_dd = 0.0
    max_dd_frac = 0.0
    for trade in trades:
        if trade.equity_after is None:
            continue
        ending = float(trade.equity_after)
        peak = max(peak, ending)
        drawdown = peak - ending
        max_dd = max(max_dd, drawdown)
        if peak > 0:
            max_dd_frac = max(max_dd_frac, drawdown / peak)
    if trades and trades[-1].equity_after is not None:
        ending = float(trades[-1].equity_after)
    return WalkEquity(
        walk_id=walk_id,
        starting_equity=round(starting_equity, 4),
        ending_equity=round(ending, 4),
        risk_fraction=risk_fraction,
        trade_count=len(trades),
        wins=wins,
        losses=losses,
        scratches=scratches,
        win_rate=round(wins / n, 4) if n else None,
        sum_r=round(sum(rs), 4) if n else None,
        mean_r=round(sum(rs) / n, 4) if n else None,
        max_drawdown=round(max_dd, 4),
        max_drawdown_frac=round(max_dd_frac, 4),
    )


def _close_trade(
    trade: PaperTrade,
    *,
    exit_index: int,
    exit_time: str,
    exit_price: float,
    exit_status: str,
    journal: Journal | None,
    equity: float,
    risk_fraction: float,
    fill_mode: str = "close",
    value_per_price_unit: float = 1.0,
) -> tuple[PaperTrade, float]:
    try:
        r_realized = round(
            risk_lib.r_multiple(trade.entry, trade.stop, exit_price), 4
        )
    except risk_lib.RiskError:
        r_realized = None
    if fill_mode == "rest":
        units = trade.units
        if units is None or units < 1:
            raise regime_walk.WalkError("rest exit requires integer units")
        pnl = rest_pnl(
            trade.side, trade.entry, exit_price, units, value_per_price_unit
        )
        equity_after = equity + pnl
    else:
        r_for_eq = 0.0 if r_realized is None else r_realized
        pnl, equity_after = risk_lib.apply_r_to_equity(equity, risk_fraction, r_for_eq)
    pnl = round(pnl, 4)
    equity_after = round(equity_after, 4)
    closed = trade.model_copy(
        update={
            "exit_index": exit_index,
            "exit_time": exit_time,
            "exit_price": exit_price,
            "exit_status": exit_status,
            "r_realized": r_realized,
            "pnl": pnl,
            "equity_after": equity_after,
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
                walk_id=trade.walk_id,
                pnl=pnl,
                equity_after=equity_after,
            )
        )
    return closed, equity_after


def _journal_entry(
    journal: Journal | None,
    goal: Goal,
    analysis: dict[str, Any],
    proposal: Any,
    verdict: Any,
    run_id: str,
    bar_time: str,
    walk_id: str,
    *,
    fill_price: float | None = None,
    fill_note: str | None = None,
) -> None:
    if journal is None:
        return
    if proposal is not None:
        updates: dict[str, Any] = {"at_time": bar_time}
        if fill_price is not None:
            updates["entry"] = fill_price
        if fill_note:
            prior = getattr(proposal, "notes", "") or ""
            updates["notes"] = f"{prior}; {fill_note}" if prior else fill_note
        proposal = proposal.model_copy(update=updates)
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
        walk_id=walk_id,
    )
    journal.append_run(record, queue_fill=False)
    price = (
        fill_price
        if fill_price is not None
        else (float(proposal.entry) if proposal is not None else 0.0)
    )
    journal.record_fill(
        SimFill(
            run_id=run_id,
            status="filled_sim",
            fill_price=price,
            ts=bar_time,
            note=fill_note or "walk fill at decision-bar close; no broker",
            walk_id=walk_id,
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
    walk_id: str | None = None,
) -> WalkResult:
    """Walk causal windows; at most one open paper ticket at a time.

    Window at index ``i`` is ``bars[: i + 1][-lookback:]``. Step is always 1.
    ``fill_mode=close``: ``pnl = equity * risk_fraction * R``.
    ``fill_mode=rest``: next-bar bid/ask fill; ``pnl`` from integer units.
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
    fill_mode = fill_mode_of(goal)
    classify = classify_fn or regime_mod.analyze_bars
    walk_id = walk_id or uuid.uuid4().hex
    starting = float(goal.balance)
    equity = starting
    trades: list[PaperTrade] = []
    open_trade: PaperTrade | None = None
    pending: RestPending | None = None
    last_i = len(series) - 1
    exit_fn = check_exit_rest if fill_mode == "rest" else check_exit

    for i in range(start_index, len(series)):
        bar = series[i]
        bar_time = str(bar.get("time") or "")

        if pending is not None and i == pending.fill_index:
            realized = realize_rest_trade(
                pending, bar, i, bar_time, equity, goal, walk_id
            )
            if realized is not None:
                open_trade, fill = realized
                _journal_entry(
                    journal,
                    goal,
                    pending.analysis,
                    pending.proposal,
                    pending.verdict,
                    open_trade.run_id,
                    bar_time,
                    walk_id,
                    fill_price=fill.price,
                    fill_note=rest_journal_note(open_trade.side, fill.units),
                )
            pending = None

        exited_here = False
        if open_trade is not None and may_check_exit(
            open_trade.entry_index, i, fill_mode
        ):
            hit = exit_fn(
                open_trade.side, open_trade.stop, open_trade.target, bar
            )
            if hit is not None:
                status, price = hit
                open_trade, equity = _close_trade(
                    open_trade,
                    exit_index=i,
                    exit_time=bar_time,
                    exit_price=price,
                    exit_status=status,
                    journal=journal,
                    equity=equity,
                    risk_fraction=goal.risk_fraction,
                    fill_mode=fill_mode,
                    value_per_price_unit=goal.value_per_price_unit,
                )
                trades.append(open_trade)
                open_trade = None
                exited_here = True

        if open_trade is None and pending is None and not exited_here:
            window = series[: i + 1][-lookback:]
            analysis = classify(window)
            analysis.setdefault("instrument", goal.instrument)
            analysis.setdefault("granularity", goal.granularity)
            entered = _maybe_enter(analysis, goal)
            if entered is not None:
                proposal, verdict = entered
                if fill_mode == "rest":
                    if i + 1 < len(series):
                        pending = RestPending(
                            fill_index=i + 1,
                            side=proposal.side,
                            play_class=proposal.play_class,
                            stop=float(proposal.stop),
                            target=float(proposal.target),
                            reasons=list(verdict.reasons),
                            proposal=proposal,
                            verdict=verdict,
                            analysis=dict(analysis),
                        )
                else:
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
                        walk_id=walk_id,
                    )
                    _journal_entry(
                        journal,
                        goal,
                        analysis,
                        proposal,
                        verdict,
                        run_id,
                        bar_time,
                        walk_id,
                    )

    if open_trade is not None:
        last = series[last_i]
        open_trade, equity = _close_trade(
            open_trade,
            exit_index=last_i,
            exit_time=str(last.get("time") or ""),
            exit_price=window_end_price(last, open_trade.side, fill_mode),
            exit_status="window_end",
            journal=journal,
            equity=equity,
            risk_fraction=goal.risk_fraction,
            fill_mode=fill_mode,
            value_per_price_unit=goal.value_per_price_unit,
        )
        trades.append(open_trade)

    return WalkResult(
        walk_id=walk_id,
        trades=trades,
        equity=summarize_equity(walk_id, trades, starting, goal.risk_fraction),
    )
