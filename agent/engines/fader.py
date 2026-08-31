"""Lien Ch. 13: Fader entry engine.

Daily ADX < 20 gates a range fade; the hourly bar must probe ≥15 pips beyond
the *prior day's* high or low and then close back inside that range. Long fades
a failed breakdown; short fades a failed breakout. Dual-TF like Ch. 8: missing
lower-TF analysis does not fire. Always after the Ch. 7 filter (``trend_waning``
blocks; play class must be ``fade_range``). Book evidence is heuristic (source
``lien-fx``, chunks 87–88). Research only; no orders. Book pip templates are
not used — tickets use ``levels.build_ticket`` + 2R.
"""

from __future__ import annotations

from typing import Any

from agent import levels as levels_mod
from agent.engines.base import EngineContext, EngineResult
from agent.schema import Citation, Goal, PlayClass
from app.regime import ADX_RANGE_IDEAL

CHAPTER = 13
DEFAULT_HTF = "D"
DEFAULT_LTF = "H1"
PROBE_PIPS = 15
BUFFER_PIPS = levels_mod.BUFFER_PIPS

CITATIONS: list[dict[str, int | str]] = [
    {"source": "lien-fx", "chunk_index": 87},
    {"source": "lien-fx", "chunk_index": 88},
]

HEURISTIC_NOTE = (
    "Ch.13 Fader is heuristic (source lien-fx). Research only; no orders."
)


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _htf_summary(analysis: dict[str, Any], granularity: str) -> dict[str, Any]:
    snap = analysis.get("snapshot") or {}
    adx = snap.get("adx") or {}
    prior = snap.get("prior_day") or {}
    return {
        "granularity": analysis.get("granularity", granularity),
        "regime": analysis.get("regime"),
        "direction": analysis.get("direction"),
        "trend_waning": bool(analysis.get("trend_waning")),
        "allowed_play_classes": list(analysis.get("allowed_play_classes") or []),
        "adx": adx.get("adx"),
        "adx_rising": adx.get("rising"),
        "prior_day_high": prior.get("high"),
        "prior_day_low": prior.get("low"),
    }


def _ltf_summary(analysis: dict[str, Any], granularity: str) -> dict[str, Any]:
    snap = analysis.get("snapshot") or {}
    return {
        "granularity": analysis.get("granularity", granularity),
        "last_close": _f(snap.get("last_close") or analysis.get("last_close")),
        "last_high": _f(snap.get("last_high")),
        "last_low": _f(snap.get("last_low")),
    }


def _result(
    signal: str,
    play_class: str,
    reason: str,
    htf: dict[str, Any],
    ltf: dict[str, Any],
    instrument: str,
    ticket: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "engine": "fader",
        "chapter": CHAPTER,
        "instrument": instrument,
        "signal": signal,
        "play_class": play_class,
        "reason": reason,
        "htf": htf,
        "ltf": ltf,
        "ticket": ticket,
        "citations": [dict(c) for c in CITATIONS],
        "note": HEURISTIC_NOTE,
    }


def _classify_probe(
    prior_high: float,
    prior_low: float,
    last_high: float,
    last_low: float,
    last_close: float,
    probe: float,
) -> str | None:
    """Return long/short if the LTF bar probed then closed back inside."""
    failed_breakdown = last_low <= prior_low - probe and last_close >= prior_low
    failed_breakout = last_high >= prior_high + probe and last_close <= prior_high
    if failed_breakdown and not failed_breakout:
        return "long"
    if failed_breakout and not failed_breakdown:
        return "short"
    return None


def _build_ticket(
    side: str,
    last_close: float,
    last_high: float,
    last_low: float,
    instrument: str,
    buffer_pips: int,
) -> dict[str, Any] | None:
    pip = levels_mod.pip_size(instrument)
    buffer = buffer_pips * pip
    if side == "long":
        return levels_mod.build_ticket(
            "long", last_close, last_low - buffer, pip, "last_close", "probe_low"
        )
    return levels_mod.build_ticket(
        "short", last_close, last_high + buffer, pip, "last_close", "probe_high"
    )


def fader_signal(
    htf_analysis: dict[str, Any],
    ltf_analysis: dict[str, Any] | None,
    instrument: str,
    *,
    buffer_pips: int = BUFFER_PIPS,
    probe_pips: int = PROBE_PIPS,
    htf_granularity: str = DEFAULT_HTF,
    ltf_granularity: str = DEFAULT_LTF,
) -> dict[str, Any]:
    """Return a Ch.13 Fader signal (``signal`` in long/short/none)."""
    htf = _htf_summary(htf_analysis, htf_granularity)
    ltf = _ltf_summary(ltf_analysis or {}, ltf_granularity)

    if htf["trend_waning"]:
        return _result(
            "none",
            "breakout_watch",
            "trend_waning: do not aggress",
            htf,
            ltf,
            instrument,
        )

    if not ltf_analysis:
        return _result(
            "none",
            "fade_range",
            "missing lower-TF analysis",
            htf,
            ltf,
            instrument,
        )

    adx_now = _f(htf.get("adx"))
    if adx_now is None or adx_now >= ADX_RANGE_IDEAL:
        return _result(
            "none",
            "fade_range",
            f"daily ADX {adx_now} is not below {ADX_RANGE_IDEAL}",
            htf,
            ltf,
            instrument,
        )

    allowed = set(htf["allowed_play_classes"])
    if "fade_range" not in allowed:
        return _result(
            "none",
            "fade_range",
            f"ADX range gate ok but regime allows {sorted(allowed)}",
            htf,
            ltf,
            instrument,
        )

    prior_high = _f(htf.get("prior_day_high"))
    prior_low = _f(htf.get("prior_day_low"))
    last_high = ltf.get("last_high")
    last_low = ltf.get("last_low")
    last_close = ltf.get("last_close")
    if (
        prior_high is None
        or prior_low is None
        or last_high is None
        or last_low is None
        or last_close is None
    ):
        return _result(
            "none",
            "fade_range",
            "prior-day or LTF high/low unavailable",
            htf,
            ltf,
            instrument,
        )

    pip = levels_mod.pip_size(instrument)
    probe = probe_pips * pip
    side = _classify_probe(
        prior_high, prior_low, last_high, last_low, last_close, probe
    )
    if side is None:
        return _result(
            "none",
            "fade_range",
            "no ≥15-pip probe beyond prior day that closed back inside",
            htf,
            ltf,
            instrument,
        )

    ticket = _build_ticket(
        side, last_close, last_high, last_low, instrument, buffer_pips
    )
    if ticket is None:
        return _result(
            "none",
            "fade_range",
            f"{side} fader probe aligned but geometry could not ticket (>=2R)",
            htf,
            ltf,
            instrument,
        )

    if side == "long":
        verb = f"probed {probe_pips} pips below prior low {prior_low} then closed back inside"
    else:
        verb = f"probed {probe_pips} pips above prior high {prior_high} then closed back inside"
    reason = f"fade_range {side}: {verb}"
    return _result(side, "fade_range", reason, htf, ltf, instrument, ticket=ticket)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def signal_confidence(
    signal: str,
    htf_analysis: dict[str, Any],
    probe_excess_pips: float | None,
) -> float:
    """Blend daily regime confidence with how far the probe exceeded 15 pips."""
    if signal not in ("long", "short"):
        return 0.0
    regime_conf = _f(htf_analysis.get("confidence")) or 0.0
    excess = probe_excess_pips if probe_excess_pips is not None else 0.0
    extremity = _clamp01(excess / 15.0)
    return round(0.5 * regime_conf + 0.5 * extremity, 3)


def _probe_excess_pips(
    out: dict[str, Any], instrument: str, probe_pips: int
) -> float | None:
    htf = out.get("htf") or {}
    ltf = out.get("ltf") or {}
    pip = levels_mod.pip_size(instrument)
    if pip <= 0:
        return None
    if out.get("signal") == "long":
        prior_low = _f(htf.get("prior_day_low"))
        last_low = _f(ltf.get("last_low"))
        if prior_low is None or last_low is None:
            return None
        return (prior_low - last_low) / pip - probe_pips
    if out.get("signal") == "short":
        prior_high = _f(htf.get("prior_day_high"))
        last_high = _f(ltf.get("last_high"))
        if prior_high is None or last_high is None:
            return None
        return (last_high - prior_high) / pip - probe_pips
    return None


class FaderEngine:
    """Ch. 13 Fader engine over an ``EngineContext`` (D + LTF)."""

    chapter = CHAPTER
    name = "fader"
    play_classes: tuple[PlayClass, ...] = ("fade_range",)

    def granularities(self, goal: Goal) -> tuple[str, ...]:
        return (goal.granularity, goal.ltf_granularity)

    def signal(self, ctx: EngineContext) -> EngineResult:
        htf_gran = ctx.goal.granularity
        ltf_gran = ctx.goal.ltf_granularity
        htf_analysis = ctx.analysis(htf_gran) or {}
        ltf_raw = ctx.analysis(ltf_gran)
        out = fader_signal(
            htf_analysis,
            ltf_raw,
            ctx.instrument,
            htf_granularity=htf_gran,
            ltf_granularity=ltf_gran,
        )
        excess = _probe_excess_pips(out, ctx.instrument, PROBE_PIPS)
        confidence = signal_confidence(out["signal"], htf_analysis, excess)
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
