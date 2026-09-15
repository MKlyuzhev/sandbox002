"""Lien Ch. 10: Fading the Double Zeros (round-number fade).

Daily Ch.7 ``fade_range`` gate, then M15 (or finer) OHLC:

1. 20-period SMA of closes on the lower TF.
2. Last close in the 10–15 pip band on the approach side of a double-zero
   figure (100 pips: 0.0100 on 4-decimal, 1.00 on JPY).
3. Long below the SMA, 10–15 pips above the figure; stop 20 pips below it.
   Short above the SMA, 10–15 pips below the figure; stop 20 pips above it.

Book scale-out (half at 1R, trail) is not used — tickets use
``levels.build_ticket`` + 2R. News/NFP/FOMC is not encoded (works best in
quiet tape; do not infer events from range fatness). Research only; no orders.
Evidence: source ``lien-fx``, chunks 77–79.
"""

from __future__ import annotations

import math
from typing import Any

from agent import levels as levels_mod
from agent.engines.base import EngineContext, EngineResult
from agent.schema import Citation, Goal, PlayClass
from app.session_clock import NEWS_NOTE

CHAPTER = 10
DEFAULT_HTF = "D"
DEFAULT_LTF = "M15"
SMA_PERIOD = 20
ENTRY_NEAR_PIPS = 10
ENTRY_FAR_PIPS = 15
STOP_PIPS = 20
INTRADAY_TFS = frozenset({"M1", "M5", "M10", "M15", "M30"})

CITATIONS: list[dict[str, int | str]] = [
    {"source": "lien-fx", "chunk_index": 77},
    {"source": "lien-fx", "chunk_index": 78},
    {"source": "lien-fx", "chunk_index": 79},
]

HEURISTIC_NOTE = (
    "Ch.10 Fading the Double Zeros is heuristic (source lien-fx). "
    f"{NEWS_NOTE}. Quiet tape / no major reports is not a coded filter. "
    "Research only; no orders."
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


def figure_step(pip: float) -> float:
    """Double-zero spacing: 100 pips."""
    return 100.0 * pip


def _snap_figure(price: float, step: float, *, up: bool) -> float:
    n = price / step
    q = math.ceil(n - 1e-12) if up else math.floor(n + 1e-12)
    return q * step


def sma_close(bars: list[dict[str, Any]], period: int = SMA_PERIOD) -> float | None:
    if period <= 0 or len(bars) < period:
        return None
    closes: list[float] = []
    for bar in bars[-period:]:
        close = _f(bar.get("close"))
        if close is None:
            return None
        closes.append(close)
    return sum(closes) / period


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
    setup: dict[str, Any] | None,
    instrument: str,
    ltf_granularity: str,
    ticket: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "engine": "double_zeros",
        "chapter": CHAPTER,
        "instrument": instrument,
        "signal": signal,
        "play_class": play_class,
        "reason": reason,
        "htf": htf,
        "ltf_granularity": ltf_granularity,
        "setup": setup,
        "ticket": ticket,
        "citations": [dict(c) for c in CITATIONS],
        "note": HEURISTIC_NOTE,
    }


def _ltf_bars_from_ctx(ctx: EngineContext, ltf_gran: str) -> list[dict[str, Any]] | None:
    bars = ctx.bars_for(ltf_gran)
    if bars:
        return bars
    analysis = ctx.analysis(ltf_gran) or {}
    extra = analysis.get("ohlc") or analysis.get("bars")
    if isinstance(extra, list) and extra:
        return extra
    return None


def _in_band(dist_pips: float) -> bool:
    return ENTRY_NEAR_PIPS - 1e-9 <= dist_pips <= ENTRY_FAR_PIPS + 1e-9


def classify_setup(
    close: float,
    sma: float,
    pip: float,
) -> dict[str, Any] | None:
    """Return long/short setup if close sits in the 10–15 pip figure band."""
    if pip <= 0:
        return None
    step = figure_step(pip)
    below = _snap_figure(close, step, up=False)
    above = _snap_figure(close, step, up=True)
    on_figure = abs(close - below) < 0.5 * pip or abs(close - above) < 0.5 * pip
    if on_figure:
        return None

    long_dist = (close - below) / pip
    short_dist = (above - close) / pip
    long_ok = close < sma and _in_band(long_dist)
    short_ok = close > sma and _in_band(short_dist)
    if long_ok == short_ok:
        return None
    if long_ok:
        return {
            "side": "long",
            "figure": below,
            "distance_pips": round(long_dist, 2),
            "sma20": sma,
            "triple_zero": abs(below / (10.0 * step) - round(below / (10.0 * step)))
            < 1e-9,
        }
    return {
        "side": "short",
        "figure": above,
        "distance_pips": round(short_dist, 2),
        "sma20": sma,
        "triple_zero": abs(above / (10.0 * step) - round(above / (10.0 * step)))
        < 1e-9,
    }


def double_zeros_signal(
    htf_analysis: dict[str, Any] | None,
    ltf_bars: list[dict[str, Any]] | None,
    instrument: str,
    *,
    htf_granularity: str = DEFAULT_HTF,
    ltf_granularity: str = DEFAULT_LTF,
) -> dict[str, Any]:
    htf = _htf_summary(htf_analysis or {}, htf_granularity)
    ltf_granularity = resolve_ltf(ltf_granularity)
    empty = _result("none", "fade_range", "", htf, None, instrument, ltf_granularity)

    if not htf_analysis:
        empty["reason"] = "missing higher-TF analysis"
        return empty
    if htf_analysis.get("trend_waning"):
        empty["reason"] = "trend_waning: do not aggress"
        return empty
    allowed = set(htf_analysis.get("allowed_play_classes") or [])
    if "fade_range" not in allowed:
        empty["reason"] = f"regime allows {sorted(allowed) or 'nothing'}; need fade_range"
        return empty
    if not ltf_bars:
        empty["reason"] = "missing lower-TF bars"
        return empty

    sma = sma_close(ltf_bars)
    if sma is None:
        empty["reason"] = f"need {SMA_PERIOD} LTF closes for SMA"
        return empty
    close = _f(ltf_bars[-1].get("close"))
    if close is None:
        empty["reason"] = "last LTF close unavailable"
        return empty

    pip = levels_mod.pip_size(instrument)
    setup = classify_setup(close, sma, pip)
    if setup is None:
        return _result(
            "none",
            "fade_range",
            "no 10–15 pip figure band with SMA filter",
            htf,
            {
                "sma20": sma,
                "last_close": close,
                "figure_step": figure_step(pip),
            },
            instrument,
            ltf_granularity,
        )

    side = str(setup["side"])
    figure = float(setup["figure"])
    stop_dist = STOP_PIPS * pip
    if side == "long":
        stop = figure - stop_dist
        entry = close
    else:
        stop = figure + stop_dist
        entry = close
    ticket = levels_mod.build_ticket(
        side, entry, stop, pip, "double_zero_band", "figure_stop"
    )
    if ticket is None:
        return _result(
            "none",
            "fade_range",
            f"{side} figure aligned but geometry could not ticket (>=2R)",
            htf,
            setup,
            instrument,
            ltf_granularity,
        )
    reason = (
        f"fade_range {side}: {setup['distance_pips']} pips from "
        f"{figure:g} (SMA20 {sma:g})"
    )
    return _result(
        side, "fade_range", reason, htf, setup, instrument, ltf_granularity, ticket=ticket
    )


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def signal_confidence(
    signal: str,
    htf_analysis: dict[str, Any],
    distance_pips: float | None,
) -> float:
    """Blend daily regime confidence with how centered the close is in 10–15 pips."""
    if signal not in ("long", "short"):
        return 0.0
    regime_conf = _f(htf_analysis.get("confidence")) or 0.0
    mid = 0.5 * (ENTRY_NEAR_PIPS + ENTRY_FAR_PIPS)
    half = 0.5 * (ENTRY_FAR_PIPS - ENTRY_NEAR_PIPS)
    dist = distance_pips if distance_pips is not None else mid
    extremity = _clamp01(1.0 - abs(dist - mid) / half) if half else 1.0
    return round(0.5 * regime_conf + 0.5 * extremity, 3)


class DoubleZerosEngine:
    """Ch. 10 double-zero fade over an ``EngineContext`` (D + M15)."""

    chapter = CHAPTER
    name = "double_zeros"
    play_classes: tuple[PlayClass, ...] = ("fade_range",)

    def granularities(self, goal: Goal) -> tuple[str, ...]:
        return (goal.granularity, resolve_ltf(goal.ltf_granularity))

    def signal(self, ctx: EngineContext) -> EngineResult:
        htf_gran = ctx.goal.granularity
        ltf_gran = resolve_ltf(ctx.goal.ltf_granularity)
        htf_analysis = ctx.analysis(htf_gran) or {}
        ltf_bars = _ltf_bars_from_ctx(ctx, ltf_gran)
        out = double_zeros_signal(
            htf_analysis,
            ltf_bars,
            ctx.instrument,
            htf_granularity=htf_gran,
            ltf_granularity=ltf_gran,
        )
        setup = out.get("setup") or {}
        confidence = signal_confidence(
            out["signal"], htf_analysis, _f(setup.get("distance_pips"))
        )
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
