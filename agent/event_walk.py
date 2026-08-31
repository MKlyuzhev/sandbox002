"""Causal one-shot event walk for Ch.9 / 14 / 16 (and tester feeds).

Unlike Ch.8 rollover-peak, these signals are fully knowable at the signal bar's
close. Each qualifying bar emits one decision stamped with its own time; the
tester fills at the next bar's open. Research only; no broker orders.
"""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Callable
from typing import Any

from agent import policy
from agent.lien_chapters import EVENT_SIGNAL
from agent.paper_walk import (
    _close_trade,
    _goal_for_walk,
    _journal_entry,
    check_exit,
    summarize_equity,
)
from agent.schema import Citation, Goal, PaperTrade, Proposal, WalkResult
from app import indicators, regime as regime_mod, regime_walk

ClassifyFn = Callable[[list[dict[str, Any]]], dict[str, Any]]


def _invoke_signal(
    signal_fn: Callable[..., dict[str, Any]],
    analysis: dict[str, Any],
    instrument: str,
    *,
    buffer_pips: int,
    granularity: str,
    bars: list[dict[str, Any]],
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "buffer_pips": buffer_pips,
        "granularity": granularity,
    }
    if "bars" in inspect.signature(signal_fn).parameters:
        kwargs["bars"] = bars
    return signal_fn(analysis, instrument, **kwargs)


def _proposal_from_signal(
    out: dict[str, Any], ticket: dict[str, Any], at_time: str
) -> Proposal:
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
        engine=str(out.get("engine") or ""),
        chapter=int(out.get("chapter") or 0),
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


def event_decisions(
    bars: list[dict[str, Any]],
    goal: Goal,
    engine: str,
    *,
    lookback: int = regime_walk.DEFAULT_LOOKBACK,
    start_index: int | None = None,
    classify_fn: ClassifyFn | None = None,
    buffer_pips: int | None = None,
) -> list[dict[str, Any]]:
    """Emit one decision per qualifying event bar (policy-gated).

    ``engine`` is ``dbb``, ``breakout20``, or ``perfect_order``.
    """
    signal_fn = EVENT_SIGNAL.get(engine)
    if signal_fn is None:
        raise regime_walk.WalkError(
            f"event_decisions: unknown engine {engine!r} (want dbb, breakout20, perfect_order)"
        )
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
    pips = 10 if buffer_pips is None else buffer_pips

    decisions: list[dict[str, Any]] = []
    for i in range(start_index, len(series)):
        window = series[: i + 1][-lookback:]
        analysis = dict(classify(window))
        analysis.setdefault("instrument", goal.instrument)
        analysis.setdefault("granularity", goal.granularity)

        out = _invoke_signal(
            signal_fn,
            analysis,
            goal.instrument,
            buffer_pips=pips,
            granularity=goal.granularity,
            bars=window,
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


def walk_event(
    bars: list[dict[str, Any]],
    goal: Goal,
    engine: str,
    *,
    lookback: int = regime_walk.DEFAULT_LOOKBACK,
    start_index: int | None = None,
    classify_fn: ClassifyFn | None = None,
    buffer_pips: int | None = None,
    journal: Any = None,
    walk_id: str | None = None,
) -> WalkResult:
    """One-position paper walk over a single-TF event engine."""
    signal_fn = EVENT_SIGNAL.get(engine)
    if signal_fn is None:
        raise regime_walk.WalkError(
            f"walk_event: unknown engine {engine!r}"
        )
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
    walk_id = walk_id or uuid.uuid4().hex
    pips = 10 if buffer_pips is None else buffer_pips
    starting = float(goal.balance)
    equity = starting
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
                open_trade, equity = _close_trade(
                    open_trade,
                    exit_index=i,
                    exit_time=bar_time,
                    exit_price=price,
                    exit_status=status,
                    journal=journal,
                    equity=equity,
                    risk_fraction=goal.risk_fraction,
                )
                trades.append(open_trade)
                open_trade = None
                exited_here = True

        if open_trade is None and not exited_here:
            window = series[: i + 1][-lookback:]
            analysis = dict(classify(window))
            analysis.setdefault("instrument", goal.instrument)
            analysis.setdefault("granularity", goal.granularity)
            out = _invoke_signal(
                signal_fn,
                analysis,
                goal.instrument,
                buffer_pips=pips,
                granularity=goal.granularity,
                bars=window,
            )
            ticket = out.get("ticket")
            if out["signal"] not in ("long", "short") or not ticket:
                continue
            proposal = _proposal_from_signal(out, ticket, bar_time)
            verdict = policy.evaluate(analysis, proposal, goal)
            if not verdict.ok:
                continue
            run_id = uuid.uuid4().hex
            open_trade = PaperTrade(
                run_id=run_id,
                entry_index=i,
                entry_time=bar_time,
                side=proposal.side,  # type: ignore[arg-type]
                play_class=proposal.play_class,
                entry=float(ticket["entry"]),
                stop=float(ticket["stop"]),
                target=float(ticket["target"]),
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
            exit_price=float(last["close"]),
            exit_status="window_end",
            journal=journal,
            equity=equity,
            risk_fraction=goal.risk_fraction,
        )
        trades.append(open_trade)

    return WalkResult(
        walk_id=walk_id,
        trades=trades,
        equity=summarize_equity(walk_id, trades, starting, goal.risk_fraction),
    )
