"""Lien Ch. 16: Perfect order entry engine.

A pulse, not a state: the SMA stack (10/20/50/100/200) must still be intact,
ADX must be rising (ideally >20), and ``ma_perfect_order_age`` must be **exactly
5** — enter five candles after the stack first forms. Later bars while the stack
holds do not re-fire. Always after the Ch. 7 regime filter: ``trend_waning``
blocks, and the play class must be ``join_trend``. Book evidence is heuristic
(source ``lien-fx``, chunks 92–93). Research only; no orders.
"""

from __future__ import annotations

from typing import Any

from agent import levels as levels_mod
from agent.engines.base import EngineContext, EngineResult
from agent.schema import Citation, Goal, PlayClass

CHAPTER = 16
DEFAULT_GRANULARITY = "D"
BUFFER_PIPS = levels_mod.BUFFER_PIPS
PULSE_AGE = 5

CITATIONS: list[dict[str, int | str]] = [
    {"source": "lien-fx", "chunk_index": 92},
    {"source": "lien-fx", "chunk_index": 93},
]

HEURISTIC_NOTE = (
    "Ch.16 Perfect order is heuristic (source lien-fx). Research only; no orders."
)


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _summary(analysis: dict[str, Any], granularity: str) -> dict[str, Any]:
    snap = analysis.get("snapshot") or {}
    adx = snap.get("adx") or {}
    return {
        "granularity": analysis.get("granularity", granularity),
        "regime": analysis.get("regime"),
        "direction": analysis.get("direction"),
        "trend_waning": bool(analysis.get("trend_waning")),
        "allowed_play_classes": list(analysis.get("allowed_play_classes") or []),
        "ma_perfect_order": snap.get("ma_perfect_order"),
        "ma_perfect_order_age": snap.get("ma_perfect_order_age"),
        "adx": adx.get("adx"),
        "adx_rising": adx.get("rising"),
    }


def _result(
    signal: str,
    play_class: str,
    reason: str,
    summary: dict[str, Any],
    instrument: str,
    ticket: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "engine": "perfect_order",
        "chapter": CHAPTER,
        "instrument": instrument,
        "signal": signal,
        "play_class": play_class,
        "reason": reason,
        "snapshot": summary,
        "ticket": ticket,
        "citations": [dict(c) for c in CITATIONS],
        "note": HEURISTIC_NOTE,
    }


def _formation_extreme(
    bars: list[dict[str, Any]] | None, age: int, side: str
) -> float | None:
    """High/low of the bar where the stack first formed (``bars[-age]``)."""
    if not bars or age < 1 or len(bars) < age:
        return None
    bar = bars[-age]
    key = "low" if side == "long" else "high"
    return _f(bar.get(key))


def _build_ticket(
    snap: dict[str, Any],
    side: str,
    last_close: float | None,
    instrument: str,
    buffer_pips: int,
    bars: list[dict[str, Any]] | None,
    age: int,
) -> dict[str, Any] | None:
    if last_close is None:
        return None
    pip = levels_mod.pip_size(instrument)
    buffer = buffer_pips * pip

    formation = _formation_extreme(bars, age, side)
    if side == "long":
        stop = (formation - buffer) if formation is not None else None
        stop_name = "formation_low"
        if stop is None or stop >= last_close:
            sma20 = _f((snap.get("sma") or {}).get("20"))
            last_low = _f(snap.get("last_low"))
            if sma20 is not None and sma20 < last_close:
                stop, stop_name = sma20 - buffer, "sma_20"
            elif last_low is not None and last_low < last_close:
                stop, stop_name = last_low - buffer, "last_low"
            else:
                return None
        return levels_mod.build_ticket(
            "long", last_close, stop, pip, "last_close", stop_name
        )

    stop = (formation + buffer) if formation is not None else None
    stop_name = "formation_high"
    if stop is None or stop <= last_close:
        sma20 = _f((snap.get("sma") or {}).get("20"))
        last_high = _f(snap.get("last_high"))
        if sma20 is not None and sma20 > last_close:
            stop, stop_name = sma20 + buffer, "sma_20"
        elif last_high is not None and last_high > last_close:
            stop, stop_name = last_high + buffer, "last_high"
        else:
            return None
    return levels_mod.build_ticket(
        "short", last_close, stop, pip, "last_close", stop_name
    )


def perfect_order_signal(
    analysis: dict[str, Any],
    instrument: str,
    *,
    buffer_pips: int = BUFFER_PIPS,
    granularity: str = DEFAULT_GRANULARITY,
    bars: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a Ch.16 perfect-order pulse signal (``signal`` in long/short/none)."""
    snap = analysis.get("snapshot") or {}
    summary = _summary(analysis, granularity)

    if summary["trend_waning"]:
        return _result(
            "none", "breakout_watch", "trend_waning: do not aggress", summary, instrument
        )

    order = snap.get("ma_perfect_order")
    age = snap.get("ma_perfect_order_age")
    adx_block = snap.get("adx") or {}
    adx_now = _f(adx_block.get("adx"))
    rising = bool(adx_block.get("rising"))

    if order not in ("up", "down"):
        return _result(
            "none",
            "breakout_watch",
            f"no SMA perfect order (stack={order})",
            summary,
            instrument,
        )
    if age != PULSE_AGE:
        return _result(
            "none",
            "join_trend",
            f"stack intact but age={age} (pulse only at age={PULSE_AGE})",
            summary,
            instrument,
        )
    if not rising:
        return _result(
            "none",
            "join_trend",
            "perfect-order age 5 but ADX is not rising",
            summary,
            instrument,
        )

    allowed = set(summary["allowed_play_classes"])
    if "join_trend" not in allowed:
        return _result(
            "none",
            "join_trend",
            f"perfect-order pulse but regime allows {sorted(allowed)}",
            summary,
            instrument,
        )

    side = "long" if order == "up" else "short"
    last_close = _f(snap.get("last_close")) or _f(analysis.get("last_close"))
    ticket = _build_ticket(
        snap, side, last_close, instrument, buffer_pips, bars, int(age)
    )
    if ticket is None:
        return _result(
            "none",
            "join_trend",
            f"{side} perfect-order pulse aligned but geometry could not ticket (>=2R)",
            summary,
            instrument,
        )

    adx_note = f"ADX={adx_now}" if adx_now is not None else "ADX unknown"
    if adx_now is not None and adx_now <= 20:
        adx_note += " (below 20; book prefers >20)"
    reason = (
        f"join_trend {side}: SMA stack {order}, age={age}, ADX rising ({adx_note})"
    )
    return _result(side, "join_trend", reason, summary, instrument, ticket=ticket)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def signal_confidence(
    signal: str,
    analysis: dict[str, Any],
) -> float:
    """Blend regime confidence with ADX strength. Non-firing signals get 0."""
    if signal not in ("long", "short"):
        return 0.0
    regime_conf = _f(analysis.get("confidence")) or 0.0
    snap = analysis.get("snapshot") or {}
    adx = _f((snap.get("adx") or {}).get("adx"))
    extremity = 0.0
    if adx is not None:
        extremity = _clamp01((adx - 20.0) / 20.0)
    return round(0.5 * regime_conf + 0.5 * extremity, 3)


class PerfectOrderEngine:
    """Ch. 16 Perfect order engine over an ``EngineContext``."""

    chapter = CHAPTER
    name = "perfect_order"
    play_classes: tuple[PlayClass, ...] = ("join_trend",)

    def granularities(self, goal: Goal) -> tuple[str, ...]:
        return (goal.granularity,)

    def signal(self, ctx: EngineContext) -> EngineResult:
        analysis = ctx.analysis(ctx.goal.granularity) or {}
        bars = ctx.bars_for(ctx.goal.granularity)
        out = perfect_order_signal(
            analysis,
            ctx.instrument,
            granularity=ctx.goal.granularity,
            bars=bars,
        )
        confidence = signal_confidence(out["signal"], analysis)
        citations = [
            Citation(source=str(c["source"]), chunk_index=int(c["chunk_index"]))
            for c in out.get("citations", [])
        ]
        return EngineResult(
            engine=self.name,
            chapter=self.chapter,
            signal=out["signal"],
            play_class=out["play_class"],
            ticket=out.get("ticket"),
            reason=out.get("reason", ""),
            confidence=confidence,
            citations=citations,
        )
