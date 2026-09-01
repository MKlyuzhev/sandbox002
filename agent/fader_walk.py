"""Causal Ch.13 Fader walk: step H1, daily ADX gate, first-fire entries.

Dual series like ``mtf_walk`` (causal HTF alignment via ``htf_index_as_of``).
The Fader signal is a one-shot failed-break on the LTF bar, so entries use
first-fire (no rollover-peak). Research only; no broker orders.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from agent import policy
from agent.engines import fader as fader_mod
from agent.mtf_walk import htf_index_as_of
from agent.paper_walk import (
    _close_trade,
    _goal_for_walk,
    _journal_entry,
    check_exit,
    summarize_equity,
)
from agent.schema import Citation, Goal, PaperTrade, Proposal, WalkResult
from agent.walk_exec import (
    RestPending,
    check_exit_rest,
    fill_mode_of,
    may_check_exit,
    realize_rest_trade,
    rest_journal_note,
    window_end_price,
)
from app import indicators, regime as regime_mod, regime_walk

ClassifyFn = Callable[[list[dict[str, Any]]], dict[str, Any]]


def _proposal_from_out(
    out: dict[str, Any], ticket: dict[str, Any], at_time: str
) -> Proposal:
    citations = [
        Citation(source=str(c["source"]), chunk_index=int(c["chunk_index"]))
        for c in out.get("citations", [])
    ]
    return Proposal(
        thesis=out.get("reason", ""),
        play_class=out.get("play_class", "fade_range"),
        side=out["signal"],
        entry=float(ticket["entry"]),
        stop=float(ticket["stop"]),
        target=float(ticket["target"]),
        at_time=at_time,
        engine="fader",
        chapter=fader_mod.CHAPTER,
        citations=citations,
        notes=out.get("note", ""),
    )


def _eval_fader_at(
    htf_series: list[dict[str, Any]],
    ltf_series: list[dict[str, Any]],
    i: int,
    *,
    lookback: int,
    goal: Goal,
    htf_classify: ClassifyFn,
    ltf_classify: ClassifyFn,
    probe_pips: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    """Return (fader_out, htf_analysis, ltf_analysis) for LTF bar ``i``."""
    if i < lookback - 1:
        return None, None, None
    bar_time = str(ltf_series[i].get("time") or "")
    htf_idx = htf_index_as_of(htf_series, bar_time)
    if htf_idx is None or htf_idx < lookback - 1:
        return None, None, None

    htf_window = htf_series[: htf_idx + 1][-lookback:]
    ltf_window = ltf_series[: i + 1][-lookback:]
    htf_analysis = dict(htf_classify(htf_window))
    ltf_analysis = dict(ltf_classify(ltf_window))
    htf_analysis.setdefault("instrument", goal.instrument)
    htf_analysis.setdefault("granularity", goal.granularity)
    ltf_analysis.setdefault("instrument", goal.instrument)
    ltf_analysis.setdefault("granularity", goal.ltf_granularity)

    out = fader_mod.fader_signal(
        htf_analysis,
        ltf_analysis,
        goal.instrument,
        probe_pips=probe_pips,
        htf_granularity=goal.granularity,
        ltf_granularity=goal.ltf_granularity,
    )
    return out, htf_analysis, ltf_analysis


def fader_decisions(
    htf_bars: list[dict[str, Any]],
    ltf_bars: list[dict[str, Any]],
    goal: Goal,
    *,
    lookback: int = regime_walk.DEFAULT_LOOKBACK,
    start_index: int | None = None,
    htf_classify_fn: ClassifyFn | None = None,
    ltf_classify_fn: ClassifyFn | None = None,
    probe_pips: int = fader_mod.PROBE_PIPS,
) -> list[dict[str, Any]]:
    """Emit one Fader decision per contiguous first-fire run (tester feed)."""
    if lookback < indicators.MIN_BARS:
        raise regime_walk.WalkError(
            f"lookback must be >= {indicators.MIN_BARS}; got {lookback}"
        )
    htf_series = regime_walk.drop_incomplete(htf_bars)
    ltf_series = regime_walk.drop_incomplete(ltf_bars)
    if start_index is None:
        start_index = lookback - 1
    if start_index < lookback - 1:
        raise regime_walk.WalkError(
            f"start_index {start_index} needs {lookback} LTF bars of history "
            f"(warmup before the test start)"
        )
    if start_index >= len(ltf_series):
        raise regime_walk.WalkError("start_index is past the last complete LTF bar")

    goal = _goal_for_walk(goal)
    htf_classify = htf_classify_fn or regime_mod.analyze_bars
    ltf_classify = ltf_classify_fn or regime_mod.analyze_bars

    decisions: list[dict[str, Any]] = []
    prev_fired = False
    for i in range(start_index, len(ltf_series)):
        out, htf_analysis, _ltf = _eval_fader_at(
            htf_series,
            ltf_series,
            i,
            lookback=lookback,
            goal=goal,
            htf_classify=htf_classify,
            ltf_classify=ltf_classify,
            probe_pips=probe_pips,
        )
        ticket = out.get("ticket") if out else None
        fired = bool(
            out
            and out["signal"] in ("long", "short")
            and ticket
            and htf_analysis is not None
        )
        if fired and not prev_fired:
            assert out is not None and ticket is not None and htf_analysis is not None
            signal_time = str(ltf_series[i].get("time") or "")
            proposal = _proposal_from_out(out, ticket, signal_time)
            verdict = policy.evaluate(htf_analysis, proposal, goal)
            if verdict.ok:
                decisions.append(
                    {
                        "signal_time": signal_time,
                        "side": out["signal"],
                        "entry": float(ticket["entry"]),
                        "stop": float(ticket["stop"]),
                        "target": float(ticket["target"]),
                        "play_class": out.get("play_class"),
                    }
                )
        prev_fired = fired
    return decisions


def walk_fader(
    htf_bars: list[dict[str, Any]],
    ltf_bars: list[dict[str, Any]],
    goal: Goal,
    *,
    lookback: int = regime_walk.DEFAULT_LOOKBACK,
    start_index: int | None = None,
    journal: Any = None,
    htf_classify_fn: ClassifyFn | None = None,
    ltf_classify_fn: ClassifyFn | None = None,
    probe_pips: int = fader_mod.PROBE_PIPS,
    walk_id: str | None = None,
) -> WalkResult:
    """Walk LTF bars with one open Fader position at a time (first-fire)."""
    if lookback < indicators.MIN_BARS:
        raise regime_walk.WalkError(
            f"lookback must be >= {indicators.MIN_BARS}; got {lookback}"
        )
    htf_series = regime_walk.drop_incomplete(htf_bars)
    ltf_series = regime_walk.drop_incomplete(ltf_bars)
    if start_index is None:
        start_index = lookback - 1
    if start_index < lookback - 1:
        raise regime_walk.WalkError(
            f"start_index {start_index} needs {lookback} LTF bars of history "
            f"(warmup before the test start)"
        )
    if start_index >= len(ltf_series):
        raise regime_walk.WalkError("start_index is past the last complete LTF bar")

    goal = _goal_for_walk(goal)
    htf_classify = htf_classify_fn or regime_mod.analyze_bars
    ltf_classify = ltf_classify_fn or regime_mod.analyze_bars
    walk_id = walk_id or uuid.uuid4().hex
    starting = float(goal.balance)
    equity = starting
    trades: list[PaperTrade] = []
    open_trade: PaperTrade | None = None
    pending: RestPending | None = None
    prev_fired = False
    last_i = len(ltf_series) - 1
    fill_mode = fill_mode_of(goal)
    exit_fn = check_exit_rest if fill_mode == "rest" else check_exit

    for i in range(start_index, len(ltf_series)):
        bar = ltf_series[i]
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
            out, htf_analysis, ltf_analysis = _eval_fader_at(
                htf_series,
                ltf_series,
                i,
                lookback=lookback,
                goal=goal,
                htf_classify=htf_classify,
                ltf_classify=ltf_classify,
                probe_pips=probe_pips,
            )
            ticket = out.get("ticket") if out else None
            fired = bool(
                out
                and out["signal"] in ("long", "short")
                and ticket
                and htf_analysis is not None
            )
            if fired and not prev_fired:
                assert out is not None and ticket is not None and htf_analysis is not None
                proposal = _proposal_from_out(out, ticket, bar_time)
                verdict = policy.evaluate(htf_analysis, proposal, goal)
                if verdict.ok:
                    regime = dict(htf_analysis)
                    regime["ltf_analysis"] = ltf_analysis
                    regime["fader"] = out
                    if fill_mode == "rest":
                        if i + 1 < len(ltf_series):
                            pending = RestPending(
                                fill_index=i + 1,
                                side=proposal.side,
                                play_class=proposal.play_class,
                                stop=float(ticket["stop"]),
                                target=float(ticket["target"]),
                                reasons=list(verdict.reasons),
                                proposal=proposal,
                                verdict=verdict,
                                analysis=regime,
                            )
                    else:
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
                            regime,
                            proposal,
                            verdict,
                            run_id,
                            bar_time,
                            walk_id,
                        )
            prev_fired = fired

    if open_trade is not None:
        last = ltf_series[last_i]
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
