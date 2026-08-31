"""Lien Ch. 14: 20-day breakout entry engine.

Not the first touch of a 20-day extreme: fire only on the rebreak after a
≥2-day pullback, within about three days of the extreme (see
``app.lien_geometry.breakout_20_state``). Join-trend only, after the Ch. 7
regime filter. Book evidence is heuristic (source ``lien-fx``, chunks 88–89).
Research only; no orders.
"""

from __future__ import annotations

from typing import Any

from agent import levels as levels_mod
from agent.engines.base import EngineContext, EngineResult
from agent.schema import Citation, Goal, PlayClass
from app import lien_geometry

CHAPTER = 14
DEFAULT_GRANULARITY = "D"
BUFFER_PIPS = levels_mod.BUFFER_PIPS

CITATIONS: list[dict[str, int | str]] = [
    {"source": "lien-fx", "chunk_index": 88},
    {"source": "lien-fx", "chunk_index": 89},
]

HEURISTIC_NOTE = (
    "Ch.14 20-day breakout is heuristic (source lien-fx). Research only; no orders."
)


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _summary(analysis: dict[str, Any], granularity: str, b20: dict[str, Any]) -> dict[str, Any]:
    return {
        "granularity": analysis.get("granularity", granularity),
        "regime": analysis.get("regime"),
        "direction": analysis.get("direction"),
        "trend_waning": bool(analysis.get("trend_waning")),
        "allowed_play_classes": list(analysis.get("allowed_play_classes") or []),
        "breakout_20": {
            "side": b20.get("side"),
            "rebreak": b20.get("rebreak"),
            "high_20": b20.get("high_20"),
            "low_20": b20.get("low_20"),
            "extreme_bars_ago": b20.get("extreme_bars_ago"),
            "pullback_bars": b20.get("pullback_bars"),
        },
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
        "engine": "breakout20",
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


def _breakout_block(
    snap: dict[str, Any], bars: list[dict[str, Any]] | None
) -> dict[str, Any]:
    block = snap.get("breakout_20")
    if isinstance(block, dict) and block.get("high_20") is not None:
        return block
    if bars:
        return lien_geometry.breakout_20_state(bars)
    return dict(block or lien_geometry.breakout_20_state([]))


def _build_ticket(
    b20: dict[str, Any],
    side: str,
    last_close: float | None,
    instrument: str,
    buffer_pips: int,
) -> dict[str, Any] | None:
    """Entry at last close; stop at the 20-day extreme ± pip buffer, 2R target."""
    if last_close is None:
        return None
    pip = levels_mod.pip_size(instrument)
    buffer = buffer_pips * pip
    if side == "long":
        rail = _f(b20.get("high_20"))
        if rail is None:
            return None
        return levels_mod.build_ticket(
            "long", last_close, rail - buffer, pip, "last_close", "high_20"
        )
    rail = _f(b20.get("low_20"))
    if rail is None:
        return None
    return levels_mod.build_ticket(
        "short", last_close, rail + buffer, pip, "last_close", "low_20"
    )


def breakout20_signal(
    analysis: dict[str, Any],
    instrument: str,
    *,
    buffer_pips: int = BUFFER_PIPS,
    granularity: str = DEFAULT_GRANULARITY,
    bars: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a Ch.14 20-day rebreak signal (``signal`` in long/short/none)."""
    snap = analysis.get("snapshot") or {}
    b20 = _breakout_block(snap, bars)
    summary = _summary(analysis, granularity, b20)

    if summary["trend_waning"]:
        return _result(
            "none", "breakout_watch", "trend_waning: do not aggress", summary, instrument
        )

    if not b20.get("rebreak") or b20.get("side") not in ("long", "short"):
        side_now = b20.get("side")
        reason = "no 20-day rebreak"
        if side_now in ("long", "short") and not b20.get("rebreak"):
            reason = f"first touch of 20-day {side_now} (not a rebreak)"
        return _result("none", "breakout_watch", reason, summary, instrument)

    allowed = set(summary["allowed_play_classes"])
    if "join_trend" not in allowed:
        return _result(
            "none",
            "join_trend",
            f"{b20.get('side')} rebreak but regime allows {sorted(allowed)}",
            summary,
            instrument,
        )

    side = str(b20["side"])
    last_close = _f(snap.get("last_close")) or _f(analysis.get("last_close"))
    ticket = _build_ticket(b20, side, last_close, instrument, buffer_pips)
    if ticket is None:
        return _result(
            "none",
            "join_trend",
            f"{side} 20-day rebreak aligned but geometry could not ticket (>=2R)",
            summary,
            instrument,
        )

    reason = (
        f"join_trend {side}: 20-day rebreak after {b20.get('pullback_bars')}-bar "
        f"pullback (extreme {b20.get('extreme_bars_ago')} bars ago)"
    )
    return _result(side, "join_trend", reason, summary, instrument, ticket=ticket)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def signal_confidence(
    signal: str,
    b20: dict[str, Any],
    last_close: float | None,
    analysis: dict[str, Any],
) -> float:
    """Blend regime confidence with how far the close cleared the 20-day level."""
    if signal not in ("long", "short") or last_close is None:
        return 0.0
    regime_conf = _f(analysis.get("confidence")) or 0.0
    if signal == "long":
        rail = _f(b20.get("high_20"))
        span = abs(last_close - rail) if rail is not None else 0.0
    else:
        rail = _f(b20.get("low_20"))
        span = abs(rail - last_close) if rail is not None else 0.0
    pip = 0.0001
    extremity = _clamp01(span / (20 * pip)) if span else 0.0
    return round(0.5 * regime_conf + 0.5 * extremity, 3)


class Breakout20Engine:
    """Ch. 14 20-day breakout engine over an ``EngineContext``."""

    chapter = CHAPTER
    name = "breakout20"
    play_classes: tuple[PlayClass, ...] = ("join_trend",)

    def granularities(self, goal: Goal) -> tuple[str, ...]:
        return (goal.granularity,)

    def signal(self, ctx: EngineContext) -> EngineResult:
        analysis = ctx.analysis(ctx.goal.granularity) or {}
        bars = ctx.bars_for(ctx.goal.granularity)
        out = breakout20_signal(
            analysis,
            ctx.instrument,
            granularity=ctx.goal.granularity,
            bars=bars,
        )
        snap = analysis.get("snapshot") or {}
        b20 = _breakout_block(snap, bars)
        last_close = _f(snap.get("last_close")) or _f(analysis.get("last_close"))
        confidence = signal_confidence(out["signal"], b20, last_close, analysis)
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
