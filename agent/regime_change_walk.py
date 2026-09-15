"""Causal walk for the structural regime-change detector.

Two products from one causal pass (window at ``i`` is ``bars[: i+1][-lookback:]``,
step 1, no look-forward on the decision):

1. A **detection log**: the staged ``regime_change`` state at every bar, plus a
   delayed evaluation of whether the Ch.7 baseline actually flipped within
   ``horizon`` bars (lead time, precision, recall) using the same causal
   flip label as :mod:`app.regime_walk`.
2. A **paper book**: a Stage-3 (``confirmed``) first-fire opens one reversal
   ticket at a time via :func:`agent.levels.build_ticket` (>=2R). Early
   warnings alone never trade. ``--fill close`` (default) fills at the
   confirming bar's mid close and checks stops/targets from the next bar on.
   ``--fill rest`` is closer to broker behaviour: the ticket rests and fills at
   the *next* bar's taking-side open (long ask / short bid) with integer units,
   exiting on the making side. Research only; no broker orders.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from agent import levels as levels_mod
from agent.paper_walk import _close_trade, _goal_for_walk, _journal_entry, check_exit, summarize_equity
from agent.schema import Citation, Goal, PaperTrade, Proposal, RiskVerdict, WalkResult
from agent.walk_exec import (
    RestPending,
    check_exit_rest,
    fill_mode_of,
    may_check_exit,
    realize_rest_trade,
    rest_journal_note,
    window_end_price,
)
from app import indicators, regime as regime_mod, regime_change, regime_walk

ClassifyFn = Callable[[list[dict[str, Any]]], dict[str, Any]]

DEFAULT_HORIZON = regime_walk.DEFAULT_HORIZON
STOP_BUFFER_PIPS = 10
STOP_LOOKBACK = indicators.HIGH_N
ACTIVE_STATES = ("early_warning", "confirming", "confirmed")


def _step_record(i: int, bar: dict[str, Any], analysis: dict[str, Any], block: dict[str, Any]) -> dict[str, Any]:
    return {
        "index": i,
        "time": bar.get("time"),
        "regime": analysis.get("regime"),
        "direction": analysis.get("direction"),
        "state": block["state"],
        "score": block["score"],
        "direction_to": block["direction_to"],
    }


def _build_ticket(
    block: dict[str, Any],
    window: list[dict[str, Any]],
    instrument: str,
) -> dict[str, Any] | None:
    """Stage-3 reversal ticket: entry at close, stop beyond the recent extreme."""
    direction_to = block.get("direction_to")
    if direction_to not in ("up", "down"):
        return None
    pip = levels_mod.pip_size(instrument)
    buffer = STOP_BUFFER_PIPS * pip
    entry = float(window[-1]["close"])
    hi, lo = indicators.n_bar_high_low(window, STOP_LOOKBACK)
    side = "long" if direction_to == "up" else "short"
    if side == "long":
        rail = lo if lo is not None else min(float(b["low"]) for b in window)
        stop = rail - buffer
    else:
        rail = hi if hi is not None else max(float(b["high"]) for b in window)
        stop = rail + buffer
    return levels_mod.build_ticket(
        side, entry, stop, pip, "regime_change", "structure_stop"
    )


def _proposal(block: dict[str, Any], ticket: dict[str, Any], at_time: str) -> Proposal:
    citations = [
        Citation(source=str(c["source"]), chunk_index=int(c["chunk_index"]))
        for c in block.get("citations", [])
    ]
    return Proposal(
        thesis=f"regime change -> {block.get('direction_to')}",
        play_class="breakout_watch",
        side=ticket["side"],
        entry=float(ticket["entry"]),
        stop=float(ticket["stop"]),
        target=float(ticket["target"]),
        at_time=at_time,
        engine="regime_change",
        chapter=None,
        citations=citations,
        notes=f"state=confirmed score={block.get('score')}",
    )


def _detection_report(
    steps: list[dict[str, Any]], horizon: int
) -> dict[str, Any]:
    """Episodes of active detection + delayed flip evaluation (causal)."""
    n = len(steps)
    # Ground-truth flip label at bar k: baseline regime changed vs k-horizon.
    for i, step in enumerate(steps):
        k = i - horizon
        step["_flip_now"] = (
            regime_walk.regime_changed(steps[k], step) if k >= 0 else None
        )

    episodes: list[dict[str, Any]] = []
    prev_active = False
    for i, step in enumerate(steps):
        active = step["state"] in ACTIVE_STATES
        if active and not prev_active:
            # Did the baseline flip within ``horizon`` bars after this warning?
            flip_index: int | None = None
            for j in range(i + 1, min(i + horizon + 1, n)):
                if regime_walk.regime_changed(step, steps[j]):
                    flip_index = j
                    break
            episodes.append(
                {
                    "index": step["index"],
                    "time": step["time"],
                    "state": step["state"],
                    "direction_to": step["direction_to"],
                    "score": step["score"],
                    "followed_by_change": flip_index is not None,
                    "lead_time": (flip_index - i) if flip_index is not None else None,
                }
            )
        prev_active = active

    tp = sum(1 for e in episodes if e["followed_by_change"])
    detections = len(episodes)
    precision = round(tp / detections, 4) if detections else None

    # Recall: flips that had an active detection within the prior ``horizon``.
    flips = [i for i in range(1, n) if regime_walk.regime_changed(steps[i - 1], steps[i])]
    covered = 0
    for fi in flips:
        lo = max(0, fi - horizon)
        if any(steps[j]["state"] in ACTIVE_STATES for j in range(lo, fi)):
            covered += 1
    recall = round(covered / len(flips), 4) if flips else None
    leads = [e["lead_time"] for e in episodes if e["lead_time"] is not None]
    mean_lead = round(sum(leads) / len(leads), 3) if leads else None

    for step in steps:
        step.pop("_flip_now", None)

    state_counts: dict[str, int] = {}
    for step in steps:
        state_counts[step["state"]] = state_counts.get(step["state"], 0) + 1

    return {
        "horizon": horizon,
        "step_count": n,
        "state_counts": state_counts,
        "detections": detections,
        "true_positives": tp,
        "precision": precision,
        "recall": recall,
        "flip_count": len(flips),
        "mean_lead_time": mean_lead,
        "episodes": episodes,
    }


def walk_regime_change(
    bars: list[dict[str, Any]],
    goal: Goal,
    *,
    lookback: int = regime_walk.DEFAULT_LOOKBACK,
    start_index: int | None = None,
    horizon: int = DEFAULT_HORIZON,
    journal: Any = None,
    classify_fn: ClassifyFn | None = None,
    walk_id: str | None = None,
) -> tuple[WalkResult, dict[str, Any]]:
    """Walk the detector causally; return (paper WalkResult, detection report)."""
    if lookback < indicators.MIN_BARS:
        raise regime_walk.WalkError(
            f"lookback must be >= {indicators.MIN_BARS}; got {lookback}"
        )
    if horizon < 1:
        raise regime_walk.WalkError("horizon must be >= 1")
    series = regime_walk.drop_incomplete(bars)
    if start_index is None:
        start_index = lookback - 1
    if start_index < lookback - 1:
        raise regime_walk.WalkError(
            f"start_index {start_index} needs {lookback} bars of history"
        )
    if start_index >= len(series):
        raise regime_walk.WalkError("start_index is past the last complete bar")

    goal = _goal_for_walk(goal)
    classify = classify_fn or regime_mod.analyze_bars
    walk_id = walk_id or uuid.uuid4().hex
    fill_mode = fill_mode_of(goal)
    exit_fn = check_exit_rest if fill_mode == "rest" else check_exit
    starting = float(goal.balance)
    equity = starting
    trades: list[PaperTrade] = []
    open_trade: PaperTrade | None = None
    pending: RestPending | None = None
    steps: list[dict[str, Any]] = []
    prev_state = "stable"
    last_i = len(series) - 1

    for i in range(start_index, len(series)):
        bar = series[i]
        bar_time = str(bar.get("time") or "")
        window = series[: i + 1][-lookback:]
        analysis = dict(classify(window))
        analysis.setdefault("instrument", goal.instrument)
        analysis.setdefault("granularity", goal.granularity)
        block = regime_change.detect(window, analysis, instrument=goal.instrument)
        steps.append(_step_record(i, bar, analysis, block))

        # A rested ticket fills at this bar's taking-side open.
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

        # Manage the open trade (rest may exit on the fill bar; close from next).
        exited_here = False
        if open_trade is not None and may_check_exit(
            open_trade.entry_index, i, fill_mode
        ):
            hit = exit_fn(open_trade.side, open_trade.stop, open_trade.target, bar)
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

        # Stage-3 first-fire: open/queue one reversal ticket at a time.
        state = block["state"]
        confirmed_first_fire = state == "confirmed" and prev_state != "confirmed"
        if (
            open_trade is None
            and pending is None
            and not exited_here
            and confirmed_first_fire
        ):
            ticket = _build_ticket(block, window, goal.instrument)
            if ticket is not None:
                proposal = _proposal(block, ticket, bar_time)
                verdict = RiskVerdict(
                    ok=True,
                    action="pending_exec",
                    reasons=[],
                    risk_fraction=goal.risk_fraction,
                    stop_distance=abs(float(ticket["entry"]) - float(ticket["stop"])),
                )
                reasons = [e["signal"] for e in block["evidence"]]
                if fill_mode == "rest":
                    if i + 1 < len(series):
                        pending = RestPending(
                            fill_index=i + 1,
                            side=ticket["side"],
                            play_class="breakout_watch",
                            stop=float(ticket["stop"]),
                            target=float(ticket["target"]),
                            reasons=reasons,
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
                        side=ticket["side"],  # type: ignore[arg-type]
                        play_class="breakout_watch",
                        entry=float(ticket["entry"]),
                        stop=float(ticket["stop"]),
                        target=float(ticket["target"]),
                        reasons=reasons,
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
        prev_state = state

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

    result = WalkResult(
        walk_id=walk_id,
        trades=trades,
        equity=summarize_equity(walk_id, trades, starting, goal.risk_fraction),
    )
    report = _detection_report(steps, horizon)
    return result, report
