"""Structural regime-change detector (staged early warning -> confirmation).

Merges the Ch.7 baseline regime (:mod:`app.regime`) with price-structure
evidence (:mod:`app.structure`) and secondary OHLCV confirmations
(:mod:`app.candles`) into a single staged verdict:

* ``stable``        - no structural pressure against the current regime,
* ``early_warning`` - leading structural cracks (Stage 1),
* ``confirming``    - one or more confirmations have printed (Stage 2),
* ``confirmed``     - enough weighted evidence to call the change (Stage 3).

Every evidence item is pinned to a corpus chunk. Weights are deterministic and
book-cited (Murphy, Edwards & Magee, Pring, Nison, Lien). The score is a
capped weighted sum: a risk-of-change reading, not a probability. Research
only; no orders.
"""

from __future__ import annotations

from typing import Any, Literal

from app import candles as candles_mod
from app import regime as regime_mod
from app import structure as structure_mod

State = Literal["stable", "early_warning", "confirming", "confirmed"]

CONFIRMED_THRESHOLD = 0.6

# Structural evidence must describe a *recent event*, not a latched state.
# ``app.structure`` reports the most recent qualifying event anywhere in the
# lookback window, so an untouched read fires for as long as the window holds
# it: measured over 21,334 causal daily bars on the seven USD majors, the
# trendline break it returned had a median age of 53 bars and fired on 85% of
# bars, which pushed ``confirmed`` to 44% of all bars and left ``stable`` at
# 0.7%. Only events no older than this many bars count as evidence.
EVENT_RECENCY_BARS = 5

# Evidence catalog: signal -> stage, weight, and the corpus chunk that grounds
# it. ``agent/structure_fidelity.py`` pins these same (source, chunk_index)
# pairs so the code and the citations stay in lockstep.
EVIDENCE_CATALOG: dict[str, dict[str, Any]] = {
    # --- Stage 1: leading structural early warnings -----------------------
    "trend_waning": {
        "stage": "early_warning",
        "weight": 0.10,
        "source": "pring-ta",
        "chunk_index": 173,
        "detail": "ADX high but rolling over; the advance is running out of steam",
    },
    "channel_far_rail_failure": {
        "stage": "early_warning",
        "weight": 0.15,
        "source": "murphy-digital",
        "chunk_index": 53,
        "detail": "price failed to reach the far channel rail (trend shifting)",
    },
    "minor_sr_break": {
        "stage": "early_warning",
        "weight": 0.15,
        "source": "edwards-magee",
        "chunk_index": 434,
        "detail": "break of a minor swing level (first step of a reversal)",
    },
    "fan_first_line": {
        "stage": "early_warning",
        "weight": 0.10,
        "source": "murphy-digital",
        "chunk_index": 49,
        "detail": "first fan trendline broken",
    },
    # --- Stage 2: confirmations ------------------------------------------
    "trendline_break_valid": {
        "stage": "confirming",
        "weight": 0.20,
        "source": "murphy-digital",
        "chunk_index": 47,
        "detail": "valid trendline break (close + time/price filter)",
    },
    "channel_basic_break": {
        "stage": "confirming",
        "weight": 0.15,
        "source": "murphy-digital",
        "chunk_index": 53,
        "detail": "basic channel rail broken (trend-change signal)",
    },
    "sr_role_reversal": {
        "stage": "confirming",
        "weight": 0.15,
        "source": "murphy-digital",
        "chunk_index": 43,
        "detail": "broken level held its reversed role on the retest",
    },
    "swing_structure_flip": {
        "stage": "confirming",
        "weight": 0.15,
        "source": "edwards-magee",
        "chunk_index": 301,
        "detail": "failed new extreme then prior-swing break (Dow flip)",
    },
    "candle_reversal_at_level": {
        "stage": "confirming",
        "weight": 0.15,
        "source": "nison-candlesticks",
        "chunk_index": 99,
        "detail": "candlestick reversal pattern at a structural level",
    },
    "volume_pickup": {
        "stage": "confirming",
        "weight": 0.10,
        "source": "edwards-magee",
        "chunk_index": 449,
        "detail": "tick-volume expansion confirming the break",
    },
    "fan_third_line": {
        "stage": "confirming",
        "weight": 0.10,
        "source": "murphy-digital",
        "chunk_index": 49,
        "detail": "third fan trendline broken (move signalled)",
    },
}


class RegimeChangeError(ValueError):
    """Raised when inputs cannot produce a baseline classification."""


def _round(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _evidence(
    signal: str,
    direction: str | None,
    extra: str = "",
    age: int | None = None,
) -> dict[str, Any]:
    spec = EVIDENCE_CATALOG[signal]
    detail = spec["detail"] if not extra else f"{spec['detail']}: {extra}"
    return {
        "signal": signal,
        "stage": spec["stage"],
        "weight": spec["weight"],
        "direction": direction,
        # Bars between the triggering event and the decision bar. ``None`` for
        # signals read off the current bar's state (no discrete event index).
        "age": age,
        "citation": {"source": spec["source"], "chunk_index": spec["chunk_index"]},
        "detail": detail,
    }


def _age(last_index: int, event_index: Any) -> int | None:
    if event_index is None:
        return None
    return last_index - int(event_index)


def _fresh(age: int | None, recency: int) -> bool:
    """True when a discrete event is recent enough to count as evidence."""
    return age is not None and 0 <= age <= recency


def _opposite(direction: str | None) -> str | None:
    if direction == "up":
        return "down"
    if direction == "down":
        return "up"
    return None


def _vote_direction(
    evidence: list[dict[str, Any]], trend_direction: str | None
) -> str | None:
    """Reversal direction: weight-summed evidence vote, tie -> opposite of trend."""
    weights: dict[str, float] = {"up": 0.0, "down": 0.0}
    for ev in evidence:
        d = ev.get("direction")
        if d in ("up", "down"):
            weights[d] += float(ev.get("weight", 0.0))
    if weights["up"] > weights["down"]:
        return "up"
    if weights["down"] > weights["up"]:
        return "down"
    return _opposite(trend_direction)


def detect(
    bars: list[dict],
    analysis: dict[str, Any] | None = None,
    *,
    instrument: str | None = None,
    recency: int = EVENT_RECENCY_BARS,
) -> dict[str, Any]:
    """Return the staged ``regime_change`` block for ``bars``.

    ``analysis`` may be a precomputed :func:`app.regime.analyze_bars` result
    (the walk passes it to avoid recomputing indicators).

    ``recency`` bounds how old a discrete structural event may be and still
    count as evidence (see :data:`EVENT_RECENCY_BARS`). Pass a large value to
    recover the old unbounded behaviour for comparison.
    """
    if analysis is None:
        analysis = regime_mod.analyze_bars(bars)

    regime = analysis.get("regime")
    # The prevailing directional bias (from the MA stack / DI) drives the
    # structural detectors even after the Ch.7 label has slipped to ``mixed``.
    trend_direction = analysis.get("direction")
    baseline_direction = trend_direction if regime == "trend" else None
    struct = structure_mod.analyze_structure(
        bars, trend_direction, instrument=instrument
    )
    levels = struct["levels"]
    candles = candles_mod.analyze_candles(bars, levels=levels)

    evidence: list[dict[str, Any]] = []
    last_index = int(struct["bar_count"]) - 1

    # --- Stage 1 -----------------------------------------------------------
    # ``trend_waning``, the channel rails and the swing read are all evaluated
    # against the decision bar's close, so they need no recency gate.
    if analysis.get("trend_waning"):
        evidence.append(_evidence("trend_waning", _opposite(trend_direction)))

    # The far-rail warning now dates the failing swing, so it is gated like any
    # other discrete event.
    channel = struct["channel"]
    far_fail = channel.get("far_rail_failure_event")
    far_age = _age(last_index, far_fail.get("index")) if far_fail else None
    if far_fail and _fresh(far_age, recency):
        evidence.append(
            _evidence(
                "channel_far_rail_failure",
                _opposite(trend_direction),
                f"fell {far_fail['gap_frac']:.2f} of width short; "
                f"rail last tagged {far_fail['bars_since_reach']} bars earlier",
                age=far_age,
            )
        )

    swing = struct["swing_flip"]
    if swing.get("broke_prior_swing") and not swing.get("flip"):
        evidence.append(_evidence("minor_sr_break", swing.get("direction_to")))

    # Fan lines carry the break index of the line that gave way; the youngest
    # broken line dates the fan signal.
    fan = struct["fan"]
    fan_ages = [
        age
        for age in (_age(last_index, b.get("break_index")) for b in fan.get("broken") or [])
        if age is not None
    ]
    fan_age = min(fan_ages) if fan_ages else None
    fan_fresh = _fresh(fan_age, recency)
    if fan.get("first_line_broken") and not fan.get("third_line_broken") and fan_fresh:
        # Fan lines break against the trend, i.e. toward the reversal.
        evidence.append(
            _evidence("fan_first_line", _opposite(trend_direction), age=fan_age)
        )

    # --- Stage 2 -----------------------------------------------------------
    tl = struct["trendline_break"]
    tl_valid = tl.get("latest_valid")
    tl_age = _age(last_index, tl_valid.get("break_index")) if tl_valid else None
    if tl_valid and _fresh(tl_age, recency):
        evidence.append(
            _evidence(
                "trendline_break_valid",
                tl_valid.get("direction"),
                f"{tl_valid['bars_beyond']} closes beyond a {tl_valid['kind']} line",
                age=tl_age,
            )
        )

    if channel.get("basic_rail_break"):
        evidence.append(
            _evidence("channel_basic_break", channel.get("reversal_direction"))
        )

    rr = struct["role_reversal"]
    rr_latest = rr.get("latest")
    rr_age = _age(last_index, rr_latest.get("retest_index")) if rr_latest else None
    if rr_latest and _fresh(rr_age, recency):
        evidence.append(
            _evidence(
                "sr_role_reversal",
                rr_latest.get("direction"),
                f"{rr_latest['from_role']}->{rr_latest['to_role']}",
                age=rr_age,
            )
        )

    if swing.get("flip"):
        evidence.append(
            _evidence("swing_structure_flip", swing.get("direction_to"))
        )

    at_level = [p for p in candles["patterns"] if p.get("at_level")]
    cand_ages = [
        age
        for age in (_age(last_index, p.get("index")) for p in at_level)
        if age is not None
    ]
    cand_age = min(cand_ages) if cand_ages else None
    if (
        candles.get("reversal_direction")
        and candles.get("any_at_level")
        and _fresh(cand_age, recency)
    ):
        names = ", ".join(p["pattern"] for p in at_level)
        evidence.append(
            _evidence(
                "candle_reversal_at_level",
                candles["reversal_direction"],
                names,
                age=cand_age,
            )
        )

    # Volume only counts as confirmation when a break/candle is also present.
    has_break_or_candle = any(
        ev["signal"]
        in (
            "trendline_break_valid",
            "channel_basic_break",
            "sr_role_reversal",
            "candle_reversal_at_level",
        )
        for ev in evidence
    )
    if candles.get("any_volume_confirmed") and has_break_or_candle:
        evidence.append(
            _evidence("volume_pickup", candles.get("reversal_direction"))
        )

    if fan.get("third_line_broken") and fan_fresh:
        evidence.append(
            _evidence("fan_third_line", _opposite(trend_direction), age=fan_age)
        )

    # --- Aggregate ---------------------------------------------------------
    warnings = [e for e in evidence if e["stage"] == "early_warning"]
    confirmations = [e for e in evidence if e["stage"] == "confirming"]
    score = min(sum(e["weight"] for e in evidence), 1.0)

    if not evidence:
        state: State = "stable"
    elif confirmations and score >= CONFIRMED_THRESHOLD:
        state = "confirmed"
    elif confirmations:
        state = "confirming"
    else:
        state = "early_warning"

    direction_to = _vote_direction(evidence, trend_direction) if evidence else None

    # A projection is only as current as the event it is measured from, so a
    # stale trendline break does not supply one.
    measured_move = None
    if channel.get("measured_move"):
        measured_move = {"source": "channel", **channel["measured_move"]}
    elif tl_valid and tl_valid.get("measured_move") and _fresh(tl_age, recency):
        measured_move = {"source": "trendline", **tl_valid["measured_move"]}

    citations = _unique_citations(evidence)

    return {
        "instrument": instrument,
        "last_time": struct["last_time"],
        "last_close": struct["last_close"],
        "state": state,
        "direction_from": baseline_direction,
        "direction_to": direction_to,
        "score": _round(score),
        "recency_bars": recency,
        "stage_counts": {
            "early_warning": len(warnings),
            "confirming": len(confirmations),
        },
        "evidence": evidence,
        "measured_move": measured_move,
        "baseline": {
            "regime": regime,
            "direction": analysis.get("direction"),
            "trend_waning": bool(analysis.get("trend_waning")),
            "confidence": analysis.get("confidence"),
            "allowed_play_classes": analysis.get("allowed_play_classes"),
        },
        "structure": struct,
        "candles": candles,
        "citations": citations,
    }


def _unique_citations(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, int]] = set()
    out: list[dict[str, Any]] = []
    for ev in evidence:
        cite = ev["citation"]
        key = (cite["source"], cite["chunk_index"])
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(cite))
    return out
