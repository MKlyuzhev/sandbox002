"""Lien Ch. 8: Multiple Time Frame (MTF) analysis entry engine.

Higher timeframe (e.g. daily) sets trend direction; the lower timeframe
(e.g. hourly) times the entry on an oscillator pullback: buy RSI dips in an
uptrend, sell RSI rallies in a downtrend. The main failure mode Lien warns
against is fading the daily trend from a lower timeframe, so this engine only
signals *with* the higher-TF direction.

Deterministic and pure: it consumes two ``regime.analyze_bars`` outputs and
never recomputes indicators or places orders. Book evidence is heuristic
(source ``lien-fx``, chunks 70-72). Research only.
"""

from __future__ import annotations

from typing import Any

from agent import levels as levels_mod
from agent.engines.base import EngineContext, EngineResult
from agent.schema import Citation, Goal, PlayClass

CHAPTER = 8
DEFAULT_HTF = "D"
DEFAULT_LTF = "H1"
RSI_OS = 30.0
RSI_OB = 70.0
BUFFER_PIPS = levels_mod.BUFFER_PIPS

# Static provenance for the Ch. 8 rules distilled here (source ``lien-fx``).
CITATIONS: list[dict[str, int | str]] = [
    {"source": "lien-fx", "chunk_index": 70},
    {"source": "lien-fx", "chunk_index": 71},
    {"source": "lien-fx", "chunk_index": 72},
]

HEURISTIC_NOTE = (
    "Ch.8 MTF is heuristic (source lien-fx). Research only; no orders."
)


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _htf_summary(analysis: dict[str, Any], granularity: str) -> dict[str, Any]:
    return {
        "granularity": analysis.get("granularity", granularity),
        "regime": analysis.get("regime"),
        "direction": analysis.get("direction"),
        "trend_waning": bool(analysis.get("trend_waning")),
        "allowed_play_classes": list(analysis.get("allowed_play_classes") or []),
    }


def _ltf_summary(analysis: dict[str, Any], granularity: str) -> dict[str, Any]:
    snap = analysis.get("snapshot") or {}
    return {
        "granularity": analysis.get("granularity", granularity),
        "rsi": _f(snap.get("rsi")),
        "last_close": _f(snap.get("last_close") or analysis.get("last_close")),
    }


def _result(
    signal: str,
    reason: str,
    htf: dict[str, Any],
    ltf: dict[str, Any],
    instrument: str,
    ticket: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "engine": "mtf",
        "chapter": CHAPTER,
        "instrument": instrument,
        "signal": signal,
        "reason": reason,
        "htf": htf,
        "ltf": ltf,
        "ticket": ticket,
        "citations": [dict(c) for c in CITATIONS],
        "note": HEURISTIC_NOTE,
    }


def mtf_signal(
    htf_analysis: dict[str, Any],
    ltf_analysis: dict[str, Any],
    instrument: str,
    *,
    rsi_os: float = RSI_OS,
    rsi_ob: float = RSI_OB,
    buffer_pips: int = BUFFER_PIPS,
    htf_granularity: str = DEFAULT_HTF,
    ltf_granularity: str = DEFAULT_LTF,
) -> dict[str, Any]:
    """Return a Ch.8 MTF signal dict (``signal`` in long/short/none).

    ``htf_analysis`` / ``ltf_analysis`` are ``regime.analyze_bars`` outputs for
    the higher and lower timeframes. A ticket is only built when the lower-TF
    oscillator pulls back in the direction of the higher-TF trend.
    """
    htf = _htf_summary(htf_analysis, htf_granularity)
    ltf = _ltf_summary(ltf_analysis, ltf_granularity)

    direction = htf["direction"]

    if htf["trend_waning"]:
        return _result(
            "none",
            "htf trend_waning: do not aggress",
            htf,
            ltf,
            instrument,
        )
    if direction not in ("up", "down"):
        return _result(
            "none",
            "htf has no clear direction",
            htf,
            ltf,
            instrument,
        )

    rsi = ltf["rsi"]
    if rsi is None:
        return _result("none", "ltf rsi unavailable", htf, ltf, instrument)

    if direction == "up":
        if rsi > rsi_os:
            return _result(
                "none",
                f"htf up but ltf rsi {rsi:.1f} > {rsi_os:.0f}: wait for a dip",
                htf,
                ltf,
                instrument,
            )
        side = "long"
    else:
        if rsi < rsi_ob:
            return _result(
                "none",
                f"htf down but ltf rsi {rsi:.1f} < {rsi_ob:.0f}: wait for a rally",
                htf,
                ltf,
                instrument,
            )
        side = "short"

    ticket = _build_ltf_ticket(ltf_analysis, side, instrument, buffer_pips)
    if ticket is None:
        return _result(
            "none",
            f"{side} pullback aligned but ltf geometry could not ticket (>=2R)",
            htf,
            ltf,
            instrument,
        )

    trigger = "dip" if side == "long" else "rally"
    reason = (
        f"htf {direction} + ltf rsi {rsi:.1f} {trigger} "
        f"(os={rsi_os:.0f}/ob={rsi_ob:.0f}) -> {side}"
    )
    return _result(side, reason, htf, ltf, instrument, ticket=ticket)


def _build_ltf_ticket(
    ltf_analysis: dict[str, Any],
    side: str,
    instrument: str,
    buffer_pips: int,
) -> dict[str, Any] | None:
    levels = levels_mod.named_levels(ltf_analysis)
    close = levels.get("last_close")
    if close is None:
        return None
    pip = levels_mod.pip_size(instrument)
    buffer = buffer_pips * pip
    if side == "long":
        if "low_n" in levels:
            rail, stop_name = levels["low_n"], "low_n"
        elif "bb_lower_1" in levels:
            rail, stop_name = levels["bb_lower_1"], "bb_lower_1"
        else:
            return None
        return levels_mod.build_ticket(
            "long", close, rail - buffer, pip, "last_close", stop_name
        )
    if "high_n" in levels:
        rail, stop_name = levels["high_n"], "high_n"
    elif "bb_upper_1" in levels:
        rail, stop_name = levels["bb_upper_1"], "bb_upper_1"
    else:
        return None
    return levels_mod.build_ticket(
        "short", close, rail + buffer, pip, "last_close", stop_name
    )


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def signal_confidence(
    signal: str,
    htf_analysis: dict[str, Any],
    rsi: float | None,
    rsi_os: float,
    rsi_ob: float,
) -> float:
    """Blend higher-TF regime confidence with lower-TF RSI extremity.

    Non-firing signals get 0. A deeper RSI dip (long) or higher rally (short)
    beyond the threshold raises confidence.
    """
    if signal not in ("long", "short") or rsi is None:
        return 0.0
    htf_conf = _f(htf_analysis.get("confidence")) or 0.0
    if signal == "long":
        extremity = _clamp01((rsi_os - rsi) / rsi_os) if rsi_os else 0.0
    else:
        span = 100.0 - rsi_ob
        extremity = _clamp01((rsi - rsi_ob) / span) if span else 0.0
    return round(0.5 * htf_conf + 0.5 * extremity, 3)


class MtfEngine:
    """Ch. 8 Multiple Time Frame engine over an ``EngineContext``."""

    chapter = CHAPTER
    name = "mtf"
    play_classes: tuple[PlayClass, ...] = ("join_trend",)

    def granularities(self, goal: Goal) -> tuple[str, ...]:
        return (goal.granularity, goal.ltf_granularity)

    def signal(self, ctx: EngineContext) -> EngineResult:
        htf_gran = ctx.goal.granularity
        ltf_gran = ctx.goal.ltf_granularity
        htf_analysis = ctx.analysis(htf_gran) or {}
        ltf_analysis = ctx.analysis(ltf_gran) or {}

        out = mtf_signal(
            htf_analysis,
            ltf_analysis,
            ctx.instrument,
            htf_granularity=htf_gran,
            ltf_granularity=ltf_gran,
        )
        rsi = (out.get("ltf") or {}).get("rsi")
        confidence = signal_confidence(
            out["signal"], htf_analysis, rsi, RSI_OS, RSI_OB
        )
        citations = [
            Citation(source=str(c["source"]), chunk_index=int(c["chunk_index"]))
            for c in out.get("citations", [])
        ]
        return EngineResult(
            engine=self.name,
            chapter=self.chapter,
            signal=out["signal"],
            play_class="join_trend",
            ticket=out.get("ticket"),
            reason=out.get("reason", ""),
            confidence=confidence,
            citations=citations,
        )
