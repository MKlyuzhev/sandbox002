"""Lien Ch. 9: Double Bollinger Bands entry engine.

Two sets of Bollinger Bands (20-period, 1sigma + 2sigma). One event keys the
whole chapter: a close crossing the 1sigma band. That single cross splits into
two sub-signals that map onto the repo's play classes:

* **join trend** - the last two closes sat inside the 1sigma bands, then a close
  pushes *out* through the 1sigma band into the outer (1sigma-2sigma) zone. Long
  breaks above the upper 1sigma band; short breaks below the lower 1sigma band.
* **fade / pick top-bottom** - the prior bar sat in the outer zone (between the
  1sigma and 2sigma bands), then a close comes *back* through the 1sigma band
  toward the middle. Long reclaims above the lower 1sigma from the lower outer
  zone; short loses the upper 1sigma from the upper outer zone.

Deterministic and pure: it consumes one ``regime.analyze_bars`` output (the
``double_bb`` block of the snapshot) and never recomputes indicators or places
orders. It always runs after the Ch. 7 regime filter, so a join_* signal only
fires in a ``join_trend`` regime and a fade_* signal only in a ``fade_range``
regime. Book evidence is heuristic (source ``lien-fx``, chunks 73-75). Research
only.
"""

from __future__ import annotations

from typing import Any

from agent import levels as levels_mod
from agent.engines.base import EngineContext, EngineResult
from agent.schema import Citation, Goal, PlayClass

CHAPTER = 9
DEFAULT_GRANULARITY = "D"
BUFFER_PIPS = levels_mod.BUFFER_PIPS

# Static provenance for the Ch. 9 rules distilled here (source ``lien-fx``).
CITATIONS: list[dict[str, int | str]] = [
    {"source": "lien-fx", "chunk_index": 73},
    {"source": "lien-fx", "chunk_index": 74},
    {"source": "lien-fx", "chunk_index": 75},
]

HEURISTIC_NOTE = (
    "Ch.9 Double Bollinger Bands is heuristic (source lien-fx). "
    "Research only; no orders."
)


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _summary(analysis: dict[str, Any], granularity: str, dbb: dict[str, Any]) -> dict[str, Any]:
    return {
        "granularity": analysis.get("granularity", granularity),
        "regime": analysis.get("regime"),
        "direction": analysis.get("direction"),
        "trend_waning": bool(analysis.get("trend_waning")),
        "allowed_play_classes": list(analysis.get("allowed_play_classes") or []),
        "zone": dbb.get("zone"),
        "prev_zone": dbb.get("prev_zone"),
        "prev2_zone": dbb.get("prev2_zone"),
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
        "engine": "dbb",
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


def _classify_cross(dbb: dict[str, Any]) -> tuple[str, str] | None:
    """Map the last three zones to a (side, play_class) trigger, or None.

    ``join_*`` needs the current bar in the outer zone with neither of the two
    prior bars already there (a fresh break through the 1sigma band). ``fade_*``
    needs the current bar back in the 1sigma range straight out of the opposite
    outer zone.
    """
    zone = dbb.get("zone")
    prev = dbb.get("prev_zone")
    prev2 = dbb.get("prev2_zone")

    if zone == "trend_up" and prev != "trend_up" and prev2 != "trend_up":
        return "long", "join_trend"
    if zone == "trend_down" and prev != "trend_down" and prev2 != "trend_down":
        return "short", "join_trend"
    if zone == "range" and prev == "trend_down":
        return "long", "fade_range"
    if zone == "range" and prev == "trend_up":
        return "short", "fade_range"
    return None


def _build_ticket(
    dbb: dict[str, Any],
    side: str,
    play_class: str,
    last_close: float | None,
    instrument: str,
    buffer_pips: int,
) -> dict[str, Any] | None:
    """Band-relative 2R ticket: entry at last close, stop off a Bollinger band.

    Join trades stop at the mid band (~1sigma of risk, close to Lien's fixed
    join stop); fade trades stop just past the 1sigma band they reclaimed.
    """
    if last_close is None:
        return None
    pip = levels_mod.pip_size(instrument)
    buffer = buffer_pips * pip
    mid = _f(dbb.get("mid"))
    upper_1 = _f(dbb.get("upper_1"))
    lower_1 = _f(dbb.get("lower_1"))

    if play_class == "join_trend":
        if mid is None:
            return None
        if side == "long":
            return levels_mod.build_ticket(
                "long", last_close, mid - buffer, pip, "last_close", "bb_mid"
            )
        return levels_mod.build_ticket(
            "short", last_close, mid + buffer, pip, "last_close", "bb_mid"
        )

    # fade_range
    if side == "long":
        if lower_1 is None:
            return None
        return levels_mod.build_ticket(
            "long", last_close, lower_1 - buffer, pip, "last_close", "bb_lower_1"
        )
    if upper_1 is None:
        return None
    return levels_mod.build_ticket(
        "short", last_close, upper_1 + buffer, pip, "last_close", "bb_upper_1"
    )


def dbb_signal(
    analysis: dict[str, Any],
    instrument: str,
    *,
    buffer_pips: int = BUFFER_PIPS,
    granularity: str = DEFAULT_GRANULARITY,
) -> dict[str, Any]:
    """Return a Ch.9 Double Bollinger signal dict (``signal`` in long/short/none).

    ``analysis`` is a ``regime.analyze_bars`` output; the ``double_bb`` block of
    its snapshot drives the 1sigma-cross detection. A ticket is only built when
    the cross aligns with the regime's allowed play classes.
    """
    snap = analysis.get("snapshot") or {}
    dbb = snap.get("double_bb") or {}
    summary = _summary(analysis, granularity, dbb)

    if summary["trend_waning"]:
        return _result(
            "none", "breakout_watch", "trend_waning: do not aggress", summary, instrument
        )

    trigger = _classify_cross(dbb)
    if trigger is None:
        return _result(
            "none",
            "breakout_watch",
            f"no 1sigma cross (zone={dbb.get('zone')} prev={dbb.get('prev_zone')})",
            summary,
            instrument,
        )
    side, play_class = trigger

    allowed = set(summary["allowed_play_classes"])
    if play_class not in allowed:
        return _result(
            "none",
            play_class,
            f"{side} {play_class} cross but regime allows {sorted(allowed)}",
            summary,
            instrument,
        )

    last_close = _f(snap.get("last_close")) or _f(analysis.get("last_close"))
    ticket = _build_ticket(
        dbb, side, play_class, last_close, instrument, buffer_pips
    )
    if ticket is None:
        return _result(
            "none",
            play_class,
            f"{side} {play_class} cross aligned but geometry could not ticket (>=2R)",
            summary,
            instrument,
        )

    if play_class == "join_trend":
        verb = "broke out through"
    else:
        verb = "reclaimed" if side == "long" else "lost"
    reason = (
        f"{play_class} {side}: close {verb} 1sigma band "
        f"(zone={dbb.get('zone')} prev={dbb.get('prev_zone')}) -> {side}"
    )
    return _result(side, play_class, reason, summary, instrument, ticket=ticket)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def signal_confidence(
    signal: str,
    play_class: str,
    dbb: dict[str, Any],
    last_close: float | None,
    analysis: dict[str, Any],
) -> float:
    """Blend regime confidence with how far the close cleared the 1sigma band.

    Non-firing signals get 0. A deeper break into the outer zone (join) or a
    firmer reclaim toward the mid band (fade) raises confidence.
    """
    if signal not in ("long", "short") or last_close is None:
        return 0.0
    regime_conf = _f(analysis.get("confidence")) or 0.0
    mid = _f(dbb.get("mid"))
    upper_1 = _f(dbb.get("upper_1"))
    upper_2 = _f(dbb.get("upper_2"))
    lower_1 = _f(dbb.get("lower_1"))
    lower_2 = _f(dbb.get("lower_2"))

    extremity = 0.0
    if play_class == "join_trend" and signal == "long" and upper_1 is not None and upper_2 is not None:
        span = upper_2 - upper_1
        extremity = _clamp01((last_close - upper_1) / span) if span > 0 else 0.0
    elif play_class == "join_trend" and signal == "short" and lower_1 is not None and lower_2 is not None:
        span = lower_1 - lower_2
        extremity = _clamp01((lower_1 - last_close) / span) if span > 0 else 0.0
    elif play_class == "fade_range" and signal == "long" and lower_1 is not None and mid is not None:
        span = mid - lower_1
        extremity = _clamp01((last_close - lower_1) / span) if span > 0 else 0.0
    elif play_class == "fade_range" and signal == "short" and upper_1 is not None and mid is not None:
        span = upper_1 - mid
        extremity = _clamp01((upper_1 - last_close) / span) if span > 0 else 0.0
    return round(0.5 * regime_conf + 0.5 * extremity, 3)


class DbbEngine:
    """Ch. 9 Double Bollinger Bands engine over an ``EngineContext``."""

    chapter = CHAPTER
    name = "dbb"
    play_classes: tuple[PlayClass, ...] = ("join_trend", "fade_range")

    def granularities(self, goal: Goal) -> tuple[str, ...]:
        return (goal.granularity,)

    def signal(self, ctx: EngineContext) -> EngineResult:
        analysis = ctx.analysis(ctx.goal.granularity) or {}
        out = dbb_signal(
            analysis,
            ctx.instrument,
            granularity=ctx.goal.granularity,
        )
        snap = analysis.get("snapshot") or {}
        dbb = snap.get("double_bb") or {}
        last_close = _f(snap.get("last_close")) or _f(analysis.get("last_close"))
        confidence = signal_confidence(
            out["signal"], out["play_class"], dbb, last_close, analysis
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
