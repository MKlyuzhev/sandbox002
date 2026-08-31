"""Causal Ch.8 MTF paper walk with rollover-peak entry confirmation.

While flat, evaluate higher-TF regime + lower-TF MTF signal each LTF bar. Track
the running max confidence among firing signals; when confidence strictly drops,
the prior bar was the peak. Because a peak is only knowable once a lower bar
follows it, entry happens **at the rollover bar** (the bar where the drop is
observed), with the ticket recomputed from that bar's geometry - never backdated
to the peak bar. Exits are managed from the next bar. At most one open position.
Research only; no broker orders.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from agent import policy
from agent.engines import mtf as mtf_mod
from agent.journal import Journal
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
EntryMode = Literal["peak", "first_fire"]


@dataclass
class _PeakCandidate:
    conf: float
    index: int
    time: str
    side: str
    ticket: dict[str, Any]
    htf_analysis: dict[str, Any]
    ltf_analysis: dict[str, Any]
    mtf_out: dict[str, Any]


@dataclass
class _BarEval:
    """Per-bar MTF evaluation. Analyses stay populated even on a non-fire so the
    rollover bar can price a fresh ticket."""

    conf: float
    candidate: _PeakCandidate | None
    htf_analysis: dict[str, Any] | None
    ltf_analysis: dict[str, Any] | None


class _PeakTracker:
    """Rollover-peak state machine shared by ``walk_mtf`` and ``mtf_decisions``.

    Feed ``(confidence, candidate)`` per LTF bar. A peak is confirmed on the
    first **strict** confidence drop (including a non-fire at ``0``); the
    confirming bar's own candidate is dropped, matching the walk semantics.
    """

    def __init__(self) -> None:
        self.peak: _PeakCandidate | None = None

    def update(
        self, conf: float, candidate: _PeakCandidate | None
    ) -> _PeakCandidate | None:
        if self.peak is not None and conf < self.peak.conf:
            confirmed = self.peak
            self.peak = None
            return confirmed
        if candidate is not None and (
            self.peak is None or candidate.conf > self.peak.conf
        ):
            self.peak = candidate
        return None


def htf_index_as_of(htf_bars: list[dict[str, Any]], t: str) -> int | None:
    """Index of the last HTF bar with ``time <= t`` (causal alignment)."""
    target = regime_walk._parse_rfc3339_utc(t)  # noqa: SLF001 — shared UTC parser
    if target is None:
        return None
    result: int | None = None
    for i, bar in enumerate(htf_bars):
        ts = regime_walk._parse_rfc3339_utc(bar.get("time"))  # noqa: SLF001
        if ts is not None and ts <= target:
            result = i
        elif ts is not None and ts > target:
            break
    return result


def _proposal_from_entry(
    peak: _PeakCandidate, ticket: dict[str, Any], at_time: str
) -> Proposal:
    """Proposal for a rollover-bar entry: peak's side/confidence, ``ticket``
    priced at the rollover bar (``at_time``)."""
    citations = [
        Citation(source=str(c["source"]), chunk_index=int(c["chunk_index"]))
        for c in peak.mtf_out.get("citations", [])
    ]
    return Proposal(
        thesis=peak.mtf_out.get("reason", ""),
        play_class="join_trend",
        side=peak.side,  # type: ignore[arg-type]
        entry=float(ticket["entry"]),
        stop=float(ticket["stop"]),
        target=float(ticket["target"]),
        at_time=at_time,
        confidence=peak.conf,
        engine="mtf",
        chapter=mtf_mod.CHAPTER,
        citations=citations,
        notes=peak.mtf_out.get("note", ""),
    )


def _eval_mtf_at(
    htf_series: list[dict[str, Any]],
    ltf_series: list[dict[str, Any]],
    i: int,
    *,
    lookback: int,
    goal: Goal,
    htf_classify: ClassifyFn,
    ltf_classify: ClassifyFn,
) -> _BarEval:
    """Evaluate LTF bar ``i``: confidence, firing candidate, and both analyses.

    The analyses are returned even when the signal does not fire so the rollover
    bar can price a fresh ticket from its own geometry.
    """
    if i < lookback - 1:
        return _BarEval(0.0, None, None, None)

    bar_time = str(ltf_series[i].get("time") or "")
    htf_idx = htf_index_as_of(htf_series, bar_time)
    if htf_idx is None or htf_idx < lookback - 1:
        return _BarEval(0.0, None, None, None)

    htf_window = htf_series[: htf_idx + 1][-lookback:]
    ltf_window = ltf_series[: i + 1][-lookback:]

    htf_analysis = dict(htf_classify(htf_window))
    ltf_analysis = dict(ltf_classify(ltf_window))
    htf_analysis.setdefault("instrument", goal.instrument)
    htf_analysis.setdefault("granularity", goal.granularity)
    ltf_analysis.setdefault("instrument", goal.instrument)
    ltf_analysis.setdefault("granularity", goal.ltf_granularity)

    mtf_out = mtf_mod.mtf_signal(
        htf_analysis,
        ltf_analysis,
        goal.instrument,
        htf_granularity=goal.granularity,
        ltf_granularity=goal.ltf_granularity,
    )
    rsi = (mtf_out.get("ltf") or {}).get("rsi")
    conf = mtf_mod.signal_confidence(
        mtf_out["signal"],
        htf_analysis,
        rsi,
        mtf_mod.RSI_OS,
        mtf_mod.RSI_OB,
    )

    if mtf_out["signal"] not in ("long", "short") or not mtf_out.get("ticket"):
        return _BarEval(conf, None, htf_analysis, ltf_analysis)

    candidate = _PeakCandidate(
        conf=conf,
        index=i,
        time=bar_time,
        side=mtf_out["signal"],
        ticket=mtf_out["ticket"],
        htf_analysis=htf_analysis,
        ltf_analysis=ltf_analysis,
        mtf_out=mtf_out,
    )
    return _BarEval(conf, candidate, htf_analysis, ltf_analysis)


def _build_entry(
    peak: _PeakCandidate,
    rollover_eval: _BarEval,
    at_time: str,
    goal: Goal,
) -> tuple[Proposal, dict[str, Any], Any] | None:
    """Recompute the ticket at the rollover bar for the peak's side and gate it.

    Returns ``(proposal, ticket, verdict)`` or ``None`` when the rollover bar has
    no usable geometry or policy rejects (e.g. the higher TF is no longer a
    join-trend at the bar we actually act on).
    """
    if rollover_eval.ltf_analysis is None or rollover_eval.htf_analysis is None:
        return None
    ticket = mtf_mod.build_ltf_ticket(
        rollover_eval.ltf_analysis, peak.side, goal.instrument
    )
    if ticket is None:
        return None
    proposal = _proposal_from_entry(peak, ticket, at_time)
    verdict = policy.evaluate(rollover_eval.htf_analysis, proposal, goal)
    if not verdict.ok:
        return None
    return proposal, ticket, verdict


def _build_entry_at_signal(
    candidate: _PeakCandidate,
    goal: Goal,
) -> tuple[Proposal, dict[str, Any], Any] | None:
    """Gate a first-fire entry on the signal bar's own ticket and analyses."""
    ticket = candidate.ticket
    if ticket is None:
        return None
    proposal = _proposal_from_entry(candidate, ticket, candidate.time)
    verdict = policy.evaluate(candidate.htf_analysis, proposal, goal)
    if not verdict.ok:
        return None
    return proposal, ticket, verdict


def _open_rollover_trade(
    peak: _PeakCandidate,
    current_i: int,
    ltf_series: list[dict[str, Any]],
    rollover_eval: _BarEval,
    goal: Goal,
    journal: Journal | None,
    walk_id: str,
) -> PaperTrade | None:
    """Enter at the rollover bar ``current_i`` (no backdating, no exit replay)."""
    bar_time = str(ltf_series[current_i].get("time") or "")
    built = _build_entry(peak, rollover_eval, bar_time, goal)
    if built is None:
        return None
    proposal, ticket, verdict = built

    run_id = uuid.uuid4().hex
    open_trade = PaperTrade(
        run_id=run_id,
        entry_index=current_i,
        entry_time=bar_time,
        side=peak.side,  # type: ignore[arg-type]
        play_class="join_trend",
        entry=float(ticket["entry"]),
        stop=float(ticket["stop"]),
        target=float(ticket["target"]),
        reasons=[
            f"mtf peak confidence {peak.conf} at {peak.time} rolled over; "
            f"entry at LTF bar {current_i}",
        ],
        walk_id=walk_id,
    )

    regime = dict(rollover_eval.htf_analysis or {})
    regime["ltf_analysis"] = rollover_eval.ltf_analysis
    regime["mtf"] = peak.mtf_out
    regime["peak_time"] = peak.time

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
    return open_trade


def _open_first_fire_trade(
    candidate: _PeakCandidate,
    goal: Goal,
    journal: Journal | None,
    walk_id: str,
) -> PaperTrade | None:
    """Enter on the first bar of a firing run (signal bar, no rollover wait)."""
    built = _build_entry_at_signal(candidate, goal)
    if built is None:
        return None
    proposal, ticket, verdict = built

    run_id = uuid.uuid4().hex
    open_trade = PaperTrade(
        run_id=run_id,
        entry_index=candidate.index,
        entry_time=candidate.time,
        side=candidate.side,  # type: ignore[arg-type]
        play_class="join_trend",
        entry=float(ticket["entry"]),
        stop=float(ticket["stop"]),
        target=float(ticket["target"]),
        reasons=[
            f"mtf first_fire confidence {candidate.conf} at LTF bar {candidate.index}",
        ],
        walk_id=walk_id,
    )

    regime = dict(candidate.htf_analysis or {})
    regime["ltf_analysis"] = candidate.ltf_analysis
    regime["mtf"] = candidate.mtf_out

    _journal_entry(
        journal,
        goal,
        regime,
        proposal,
        verdict,
        run_id,
        candidate.time,
        walk_id,
    )
    return open_trade


def walk_mtf(
    htf_bars: list[dict[str, Any]],
    ltf_bars: list[dict[str, Any]],
    goal: Goal,
    *,
    lookback: int = regime_walk.DEFAULT_LOOKBACK,
    start_index: int | None = None,
    entry_mode: EntryMode = "peak",
    journal: Journal | None = None,
    classify_fn: ClassifyFn | None = None,
    htf_classify_fn: ClassifyFn | None = None,
    ltf_classify_fn: ClassifyFn | None = None,
    walk_id: str | None = None,
) -> WalkResult:
    """Walk LTF bars; paper-manage exits.

    ``entry_mode=peak`` (default): confirm MTF peaks on confidence rollover and
    enter at the rollover bar. ``entry_mode=first_fire``: enter on the first bar
    of each contiguous firing run (no rollover wait).
    """
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
    default_classify = classify_fn or regime_mod.analyze_bars
    htf_classify = htf_classify_fn or default_classify
    ltf_classify = ltf_classify_fn or default_classify
    walk_id = walk_id or uuid.uuid4().hex

    starting = float(goal.balance)
    equity = starting
    trades: list[PaperTrade] = []
    open_trade: PaperTrade | None = None
    tracker = _PeakTracker()
    prev_had_candidate = False
    last_i = len(ltf_series) - 1

    for i in range(start_index, len(ltf_series)):
        bar = ltf_series[i]
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
            bar_eval = _eval_mtf_at(
                htf_series,
                ltf_series,
                i,
                lookback=lookback,
                goal=goal,
                htf_classify=htf_classify,
                ltf_classify=ltf_classify,
            )
            if entry_mode == "first_fire":
                if bar_eval.candidate is not None and not prev_had_candidate:
                    open_trade = _open_first_fire_trade(
                        bar_eval.candidate,
                        goal,
                        journal,
                        walk_id,
                    )
            else:
                confirmed = tracker.update(bar_eval.conf, bar_eval.candidate)
                if confirmed is not None:
                    open_trade = _open_rollover_trade(
                        confirmed,
                        i,
                        ltf_series,
                        bar_eval,
                        goal,
                        journal,
                        walk_id,
                    )
            prev_had_candidate = bar_eval.candidate is not None

    if open_trade is not None:
        last = ltf_series[last_i]
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


def _decision_from_entry(
    peak: _PeakCandidate, ticket: dict[str, Any], signal_time: str
) -> dict[str, Any]:
    """Feed row for a rollover-bar entry. ``signal_time`` is the rollover bar
    (where the peak became knowable); the ticket is priced at that bar."""
    return {
        "signal_time": signal_time,
        "side": peak.side,
        "entry": float(ticket["entry"]),
        "stop": float(ticket["stop"]),
        "target": float(ticket["target"]),
        "confidence": peak.conf,
    }


def mtf_decisions(
    htf_bars: list[dict[str, Any]],
    ltf_bars: list[dict[str, Any]],
    goal: Goal,
    *,
    lookback: int = regime_walk.DEFAULT_LOOKBACK,
    start_index: int | None = None,
    entry_mode: EntryMode = "peak",
    htf_classify_fn: ClassifyFn | None = None,
    ltf_classify_fn: ClassifyFn | None = None,
) -> list[dict[str, Any]]:
    """Emit MTF entries for the tester feed, ungated by open-trade state.

    ``entry_mode=peak`` (default): same rollover-peak logic as :func:`walk_mtf`;
    each decision is stamped with the **rollover bar** time and a ticket
    recomputed at that bar. Unconfirmed peaks at the series end are dropped.

    ``entry_mode=first_fire``: one decision per contiguous firing run, stamped
    with the **signal bar** time and that bar's ticket.
    """
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

    tracker = _PeakTracker()
    decisions: list[dict[str, Any]] = []
    prev_had_candidate = False

    for i in range(start_index, len(ltf_series)):
        bar_eval = _eval_mtf_at(
            htf_series,
            ltf_series,
            i,
            lookback=lookback,
            goal=goal,
            htf_classify=htf_classify,
            ltf_classify=ltf_classify,
        )
        if entry_mode == "first_fire":
            if bar_eval.candidate is not None and not prev_had_candidate:
                built = _build_entry_at_signal(bar_eval.candidate, goal)
                if built is not None:
                    _proposal, ticket, _verdict = built
                    decisions.append(
                        _decision_from_entry(
                            bar_eval.candidate, ticket, bar_eval.candidate.time
                        )
                    )
        else:
            confirmed = tracker.update(bar_eval.conf, bar_eval.candidate)
            if confirmed is not None:
                signal_time = str(ltf_series[i].get("time") or "")
                built = _build_entry(confirmed, bar_eval, signal_time, goal)
                if built is not None:
                    _proposal, ticket, _verdict = built
                    decisions.append(
                        _decision_from_entry(confirmed, ticket, signal_time)
                    )
        prev_had_candidate = bar_eval.candidate is not None

    return decisions
