"""Causal Ch.8 MTF paper walk with rollover-peak entry confirmation.

While flat, evaluate higher-TF regime + lower-TF MTF signal each LTF bar. Track
the running max confidence among firing signals; when confidence strictly drops,
confirm the peak bar (not the rollover bar), journal, and paper-manage stop /
target until flat. At most one open position. Research only; no broker orders.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

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

    def flush(self) -> _PeakCandidate | None:
        pending = self.peak
        self.peak = None
        return pending


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


def _proposal_from_peak(peak: _PeakCandidate) -> Proposal:
    ticket = peak.ticket
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
        at_time=peak.time,
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
) -> tuple[float, _PeakCandidate | None]:
    """Return (confidence, peak candidate) for LTF bar ``i``."""
    if i < lookback - 1:
        return 0.0, None

    bar_time = str(ltf_series[i].get("time") or "")
    htf_idx = htf_index_as_of(htf_series, bar_time)
    if htf_idx is None or htf_idx < lookback - 1:
        return 0.0, None

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
        return conf, None

    return conf, _PeakCandidate(
        conf=conf,
        index=i,
        time=bar_time,
        side=mtf_out["signal"],
        ticket=mtf_out["ticket"],
        htf_analysis=htf_analysis,
        ltf_analysis=ltf_analysis,
        mtf_out=mtf_out,
    )


def _confirm_peak(
    peak: _PeakCandidate,
    current_i: int,
    ltf_series: list[dict[str, Any]],
    goal: Goal,
    journal: Journal | None,
    walk_id: str,
    trades: list[PaperTrade],
    equity: float,
) -> tuple[PaperTrade | None, float]:
    """Journal peak entry and replay exits from ``peak.index + 1`` through ``current_i``."""
    proposal = _proposal_from_peak(peak)
    verdict = policy.evaluate(peak.htf_analysis, proposal, goal)
    if not verdict.ok:
        return None, equity

    run_id = uuid.uuid4().hex
    open_trade = PaperTrade(
        run_id=run_id,
        entry_index=peak.index,
        entry_time=peak.time,
        side=peak.side,  # type: ignore[arg-type]
        play_class="join_trend",
        entry=float(peak.ticket["entry"]),
        stop=float(peak.ticket["stop"]),
        target=float(peak.ticket["target"]),
        reasons=[
            f"mtf peak confidence {peak.conf} confirmed at LTF bar {current_i}",
        ],
        walk_id=walk_id,
    )

    regime = dict(peak.htf_analysis)
    regime["ltf_analysis"] = peak.ltf_analysis
    regime["mtf"] = peak.mtf_out

    _journal_entry(
        journal,
        goal,
        regime,
        proposal,
        verdict,
        run_id,
        peak.time,
        walk_id,
    )

    for j in range(peak.index + 1, current_i + 1):
        bar = ltf_series[j]
        bar_time = str(bar.get("time") or "")
        hit = check_exit(
            open_trade.side, open_trade.stop, open_trade.target, bar
        )
        if hit is not None:
            status, price = hit
            open_trade, equity = _close_trade(
                open_trade,
                exit_index=j,
                exit_time=bar_time,
                exit_price=price,
                exit_status=status,
                journal=journal,
                equity=equity,
                risk_fraction=goal.risk_fraction,
            )
            trades.append(open_trade)
            return None, equity

    return open_trade, equity


def walk_mtf(
    htf_bars: list[dict[str, Any]],
    ltf_bars: list[dict[str, Any]],
    goal: Goal,
    *,
    lookback: int = regime_walk.DEFAULT_LOOKBACK,
    start_index: int | None = None,
    journal: Journal | None = None,
    classify_fn: ClassifyFn | None = None,
    htf_classify_fn: ClassifyFn | None = None,
    ltf_classify_fn: ClassifyFn | None = None,
    walk_id: str | None = None,
) -> WalkResult:
    """Walk LTF bars; confirm MTF peaks on confidence rollover; paper-manage exits."""
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
            conf, candidate = _eval_mtf_at(
                htf_series,
                ltf_series,
                i,
                lookback=lookback,
                goal=goal,
                htf_classify=htf_classify,
                ltf_classify=ltf_classify,
            )
            confirmed = tracker.update(conf, candidate)
            if confirmed is not None:
                open_trade, equity = _confirm_peak(
                    confirmed,
                    i,
                    ltf_series,
                    goal,
                    journal,
                    walk_id,
                    trades,
                    equity,
                )

    if open_trade is None:
        tail = tracker.flush()
        if tail is not None:
            open_trade, equity = _confirm_peak(
                tail,
                last_i,
                ltf_series,
                goal,
                journal,
                walk_id,
                trades,
                equity,
            )

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


def _decision_from_peak(peak: _PeakCandidate) -> dict[str, Any]:
    return {
        "signal_time": peak.time,
        "side": peak.side,
        "entry": float(peak.ticket["entry"]),
        "stop": float(peak.ticket["stop"]),
        "target": float(peak.ticket["target"]),
        "confidence": peak.conf,
    }


def mtf_decisions(
    htf_bars: list[dict[str, Any]],
    ltf_bars: list[dict[str, Any]],
    goal: Goal,
    *,
    lookback: int = regime_walk.DEFAULT_LOOKBACK,
    start_index: int | None = None,
    htf_classify_fn: ClassifyFn | None = None,
    ltf_classify_fn: ClassifyFn | None = None,
) -> list[dict[str, Any]]:
    """Emit every confirmed rollover-peak entry, ungated by open-trade state.

    Same causal peak logic as :func:`walk_mtf` (shared :class:`_PeakTracker`),
    but instead of simulating fills it returns one decision dict per confirmed
    peak that passes the policy gate. Used to build the MT4 Strategy Tester
    decision feed, where the tester (not this walk) owns position state.
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

    def _emit(peak: _PeakCandidate) -> None:
        proposal = _proposal_from_peak(peak)
        verdict = policy.evaluate(peak.htf_analysis, proposal, goal)
        if verdict.ok:
            decisions.append(_decision_from_peak(peak))

    for i in range(start_index, len(ltf_series)):
        conf, candidate = _eval_mtf_at(
            htf_series,
            ltf_series,
            i,
            lookback=lookback,
            goal=goal,
            htf_classify=htf_classify,
            ltf_classify=ltf_classify,
        )
        confirmed = tracker.update(conf, candidate)
        if confirmed is not None:
            _emit(confirmed)

    tail = tracker.flush()
    if tail is not None:
        _emit(tail)

    return decisions
