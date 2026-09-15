"""Lien Ch. 11: Waiting for the Deal (London stop-hunt then reverse).

Daily Ch.7 gate, then M15 (or finer) OHLC + calendar clock:

1. Frankfurt→London power hour range (DST-aware ``frankfurt`` clock).
2. After London open, a ≥25-pip hunt beyond that range.
3. Reverse through the opposite rail; pending 10 pips beyond the rail.

Book pip templates (50 / 105) are not used — tickets use ``levels.build_ticket``
+ 2R. News/FOMC is not encoded. Optional skip of new tickets on a scheduled
FOMC statement London date is documented hygiene (LIEN_FX_STRATEGIES Ch.11),
not this function. Research only; no orders.
Evidence: source ``lien-fx``, chunks 80–82.
"""

from __future__ import annotations

from typing import Any

from agent import levels as levels_mod
from agent.engines.base import EngineContext, EngineResult
from agent.schema import Citation, Goal, PlayClass
from app.session_clock import (
    NEWS_NOTE,
    ClockMode,
    waiting_deal_state,
)

CHAPTER = 11
DEFAULT_HTF = "D"
DEFAULT_LTF = "M15"
HUNT_PIPS = 25
ENTRY_BUFFER_PIPS = 10
STOP_PIPS = 25
INTRADAY_TFS = frozenset({"M1", "M5", "M10", "M15", "M30"})

CITATIONS: list[dict[str, int | str]] = [
    {"source": "lien-fx", "chunk_index": 80},
    {"source": "lien-fx", "chunk_index": 81},
    {"source": "lien-fx", "chunk_index": 82},
]

HEURISTIC_NOTE = (
    "Ch.11 Waiting for the Deal is heuristic (source lien-fx). "
    f"{NEWS_NOTE}. Research only; no orders."
)


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_ltf(ltf_granularity: str | None) -> str:
    """Honor M1–M30; otherwise the book-scale default (M15)."""
    key = (ltf_granularity or "").strip().upper()
    if key in INTRADAY_TFS:
        return key
    return DEFAULT_LTF


def _htf_summary(analysis: dict[str, Any], granularity: str) -> dict[str, Any]:
    return {
        "granularity": analysis.get("granularity", granularity),
        "regime": analysis.get("regime"),
        "direction": analysis.get("direction"),
        "trend_waning": bool(analysis.get("trend_waning")),
        "allowed_play_classes": list(analysis.get("allowed_play_classes") or []),
        "confidence": _f(analysis.get("confidence")),
    }


def _result(
    signal: str,
    play_class: str,
    reason: str,
    htf: dict[str, Any],
    deal: dict[str, Any],
    instrument: str,
    ltf_granularity: str,
    ticket: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "engine": "waiting_deal",
        "chapter": CHAPTER,
        "instrument": instrument,
        "signal": signal,
        "play_class": play_class,
        "reason": reason,
        "htf": htf,
        "ltf_granularity": ltf_granularity,
        "deal": deal,
        "ticket": ticket,
        "citations": [dict(c) for c in CITATIONS],
        "note": HEURISTIC_NOTE,
    }


def _play_class(allowed: set[str]) -> str | None:
    if "fade_range" in allowed:
        return "fade_range"
    if "breakout_watch" in allowed:
        return "breakout_watch"
    return None


def _ltf_bars_from_ctx(ctx: EngineContext, ltf_gran: str) -> list[dict[str, Any]] | None:
    bars = ctx.bars_for(ltf_gran)
    if bars:
        return bars
    analysis = ctx.analysis(ltf_gran) or {}
    extra = analysis.get("ohlc") or analysis.get("bars")
    if isinstance(extra, list) and extra:
        return extra
    return None


def waiting_deal_signal(
    htf_analysis: dict[str, Any],
    ltf_bars: list[dict[str, Any]] | None,
    instrument: str,
    *,
    hunt_pips: int = HUNT_PIPS,
    buffer_pips: int = ENTRY_BUFFER_PIPS,
    stop_pips: int = STOP_PIPS,
    clock: ClockMode = "frankfurt",
    htf_granularity: str = DEFAULT_HTF,
    ltf_granularity: str = DEFAULT_LTF,
) -> dict[str, Any]:
    """Return a Ch.11 signal (``signal`` in long/short/none)."""
    htf = _htf_summary(htf_analysis, htf_granularity)
    deal: dict[str, Any] = {"clock": clock, "note": NEWS_NOTE}
    play = _play_class(set(htf["allowed_play_classes"])) or "breakout_watch"

    if htf["trend_waning"]:
        return _result(
            "none",
            "breakout_watch",
            "trend_waning: do not aggress",
            htf,
            deal,
            instrument,
            ltf_granularity,
        )

    allowed = set(htf["allowed_play_classes"])
    chosen = _play_class(allowed)
    if chosen is None:
        return _result(
            "none",
            play,
            f"regime allows {sorted(allowed)}; need fade_range or breakout_watch",
            htf,
            deal,
            instrument,
            ltf_granularity,
        )

    if not ltf_bars:
        return _result(
            "none",
            chosen,
            "missing lower-TF bars",
            htf,
            deal,
            instrument,
            ltf_granularity,
        )

    pip = levels_mod.pip_size(instrument)
    deal = waiting_deal_state(
        ltf_bars,
        pip=pip,
        hunt_pips=float(hunt_pips),
        entry_buffer_pips=float(buffer_pips),
        stop_pips=float(stop_pips),
        clock=clock,
    )
    if not deal.get("reversed") or deal.get("pending_side") not in ("long", "short"):
        return _result(
            "none",
            chosen,
            deal.get("reason") or "no hunt-then-reverse",
            htf,
            deal,
            instrument,
            ltf_granularity,
        )

    side = str(deal["pending_side"])
    entry = _f(deal.get("pending_entry"))
    stop = _f(deal.get("pending_stop"))
    if entry is None or stop is None:
        return _result(
            "none",
            chosen,
            "pending geometry unavailable",
            htf,
            deal,
            instrument,
            ltf_granularity,
        )

    ticket = levels_mod.build_ticket(
        side,
        entry,
        stop,
        pip,
        "power_hour_rail",
        "power_hour_stop",
    )
    if ticket is None:
        return _result(
            "none",
            chosen,
            f"{side} reverse aligned but geometry could not ticket (>=2R)",
            htf,
            deal,
            instrument,
            ltf_granularity,
        )

    pair_note = ""
    if (instrument or "").replace("/", "_").upper() != "GBP_USD":
        pair_note = " (book examples are GBP_USD)"
    reason = f"{chosen} {side}: {deal.get('reason')}{pair_note}"
    return _result(
        side, chosen, reason, htf, deal, instrument, ltf_granularity, ticket=ticket
    )


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def signal_confidence(
    signal: str,
    htf_analysis: dict[str, Any],
    hunt_excess_pips: float | None,
) -> float:
    """Blend daily regime confidence with how far the hunt exceeded 25 pips."""
    if signal not in ("long", "short"):
        return 0.0
    regime_conf = _f(htf_analysis.get("confidence")) or 0.0
    excess = hunt_excess_pips if hunt_excess_pips is not None else 0.0
    extremity = _clamp01(excess / float(HUNT_PIPS))
    return round(0.5 * regime_conf + 0.5 * extremity, 3)


def _hunt_excess_pips(out: dict[str, Any]) -> float | None:
    deal = out.get("deal") or {}
    printed = _f(deal.get("hunt_pips"))
    if printed is None:
        return None
    return printed - HUNT_PIPS


class WaitingDealEngine:
    """Ch. 11 Waiting for the Deal over an ``EngineContext`` (D + M15)."""

    chapter = CHAPTER
    name = "waiting_deal"
    play_classes: tuple[PlayClass, ...] = ("fade_range", "breakout_watch")

    def granularities(self, goal: Goal) -> tuple[str, ...]:
        return (goal.granularity, resolve_ltf(goal.ltf_granularity))

    def signal(self, ctx: EngineContext) -> EngineResult:
        htf_gran = ctx.goal.granularity
        ltf_gran = resolve_ltf(ctx.goal.ltf_granularity)
        htf_analysis = ctx.analysis(htf_gran) or {}
        ltf_bars = _ltf_bars_from_ctx(ctx, ltf_gran)
        out = waiting_deal_signal(
            htf_analysis,
            ltf_bars,
            ctx.instrument,
            htf_granularity=htf_gran,
            ltf_granularity=ltf_gran,
        )
        confidence = signal_confidence(out["signal"], htf_analysis, _hunt_excess_pips(out))
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
