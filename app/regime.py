"""Lien Chapter 7 regime checklist (trend vs range).

Pure functions: indicator snapshot in, structured classification out.
Counts journal-style checks rather than a magic composite score.
See docs/LIEN_FX_STRATEGIES.md.
"""

from __future__ import annotations

from typing import Any, Literal

from app import indicators

Regime = Literal["trend", "range", "mixed"]
PlayClass = Literal["join_trend", "fade_range", "breakout_watch"]

ADX_TREND = 25.0
ADX_RANGE_IDEAL = 20.0
ADX_WANING_FROM = 40.0
RSI_OB = 70.0
RSI_OS = 30.0
STOCH_OB = 80.0
STOCH_OS = 20.0


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _direction(snap: dict[str, Any]) -> str | None:
    order = snap.get("ma_perfect_order")
    if order in ("up", "down"):
        return order
    adx = snap.get("adx") or {}
    pdi, mdi = _f(adx.get("plus_di")), _f(adx.get("minus_di"))
    if pdi is not None and mdi is not None and pdi != mdi:
        return "up" if pdi > mdi else "down"
    vs20 = (snap.get("close_vs_sma") or {}).get("20")
    if vs20 == "above":
        return "up"
    if vs20 == "below":
        return "down"
    zone = (snap.get("bollinger") or {}).get("zone")
    if zone == "trend_up":
        return "up"
    if zone == "trend_down":
        return "down"
    return None


def _oscillators_aligned(snap: dict[str, Any], direction: str | None) -> bool:
    if direction is None:
        return False
    rsi = _f(snap.get("rsi"))
    stoch = snap.get("stoch") or {}
    k, d = _f(stoch.get("k")), _f(stoch.get("d"))
    hist = _f((snap.get("macd") or {}).get("hist"))
    if direction == "up":
        rsi_ok = rsi is not None and rsi > 50
        stoch_ok = k is not None and (k > 50 or (d is not None and k >= d))
        macd_ok = hist is not None and hist > 0
    else:
        rsi_ok = rsi is not None and rsi < 50
        stoch_ok = k is not None and (k < 50 or (d is not None and k <= d))
        macd_ok = hist is not None and hist < 0
    return sum((rsi_ok, stoch_ok, macd_ok)) >= 2


def _ma_break(snap: dict[str, Any], direction: str | None) -> bool:
    vs = snap.get("close_vs_sma") or {}
    if direction == "up":
        return vs.get("50") == "above" or vs.get("100") == "above"
    if direction == "down":
        return vs.get("50") == "below" or vs.get("100") == "below"
    return False


def classify(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Journal-style X-count → regime, play classes, and notes."""
    adx_block = snapshot.get("adx") or {}
    adx = _f(adx_block.get("adx"))
    slope = _f(adx_block.get("slope"))
    rising = bool(adx_block.get("rising"))
    falling = slope is not None and slope < 0
    bb = snapshot.get("bollinger") or {}
    zone = bb.get("zone") or "unknown"
    rsi = _f(snapshot.get("rsi"))
    stoch_k = _f((snapshot.get("stoch") or {}).get("k"))
    direction = _direction(snapshot)
    order = snapshot.get("ma_perfect_order")

    outer_tag = zone in ("trend_up", "trend_down")
    inside_1s = zone == "range"

    trend_checks = {
        "adx_above_25": bool(adx is not None and adx > ADX_TREND),
        "adx_rising": rising,
        "outer_band_tag": outer_tag,
        "ma_break": _ma_break(snapshot, direction),
        "perfect_order": order in ("up", "down"),
        "oscillators_aligned": _oscillators_aligned(snapshot, direction),
    }
    range_checks = {
        "adx_below_25": bool(adx is not None and adx < ADX_TREND),
        "adx_below_20": bool(adx is not None and adx < ADX_RANGE_IDEAL),
        "adx_falling": falling,
        "inside_1sigma": inside_1s,
        "rsi_extreme": bool(rsi is not None and (rsi >= RSI_OB or rsi <= RSI_OS)),
        "stoch_extreme": bool(
            stoch_k is not None and (stoch_k >= STOCH_OB or stoch_k <= STOCH_OS)
        ),
    }
    trend_n = sum(1 for v in trend_checks.values() if v)
    range_n = sum(1 for v in range_checks.values() if v)

    trend_waning = bool(
        adx is not None
        and adx > ADX_TREND
        and falling
        and (adx >= ADX_WANING_FROM or (slope is not None and adx - slope >= ADX_WANING_FROM))
    )
    # Also flag if ADX > 25, falling, and was near the 40 caution zone
    # even when current ADX has already slipped: slope lookback covers this
    # when adx - slope >= 40.
    if adx is not None and adx > ADX_TREND and falling and adx >= 35:
        trend_waning = True

    notes: list[str] = []
    if snapshot.get("sma_missing"):
        notes.append(f"sma_missing={snapshot['sma_missing']}")
    if snapshot.get("risk_reversals") == "unavailable":
        notes.append("risk_reversals=unavailable")
    if snapshot.get("implied_vol") == "unavailable":
        notes.append("implied_vol=unavailable")

    regime: Regime
    plays: list[PlayClass]
    if trend_waning:
        regime = "mixed"
        plays = ["breakout_watch"]
        notes.append("trend_waning: ADX>25 but falling from a high level; do not aggress")
    elif trend_n >= 3 and adx is not None and adx > ADX_TREND:
        regime = "trend"
        plays = ["join_trend"]
    elif range_n >= 3 and adx is not None and adx < ADX_TREND:
        regime = "range"
        plays = ["fade_range"]
    elif inside_1s and adx is not None and adx < ADX_TREND:
        regime = "range"
        plays = ["fade_range"]
    elif outer_tag and adx is not None and adx > ADX_TREND:
        regime = "trend"
        plays = ["join_trend"]
    else:
        regime = "mixed"
        plays = ["breakout_watch"]

    denom = max(trend_n + range_n, 1)
    if regime == "trend":
        confidence = round(trend_n / 6.0, 3)
    elif regime == "range":
        confidence = round(range_n / 6.0, 3)
    else:
        confidence = round(min(trend_n, range_n) / denom, 3)

    return {
        "regime": regime,
        "direction": direction,
        "confidence": confidence,
        "trend_waning": trend_waning,
        "trend_checks": trend_checks,
        "range_checks": range_checks,
        "trend_x_count": trend_n,
        "range_x_count": range_n,
        "ma_perfect_order": order,
        "allowed_play_classes": plays,
        "notes": notes,
        "risk_reversals": snapshot.get("risk_reversals", "unavailable"),
        "implied_vol": snapshot.get("implied_vol", "unavailable"),
    }


def analyze_bars(bars: list[dict]) -> dict[str, Any]:
    """Snapshot + classification. Raises ``IndicatorError`` if too few bars."""
    snap = indicators.snapshot(bars)
    classified = classify(snap)
    return {
        **classified,
        "snapshot": snap,
        "bar_count": snap["bar_count"],
        "last_time": snap["last_time"],
        "last_close": snap["last_close"],
    }
