"""OHLC geometry for Lien Ch. 13/14/16 entry engines.

Pure functions: bars in, JSON-friendly dicts out. No network, no orders.
Engines consume these blocks (also attached to ``indicators.snapshot``).
"""

from __future__ import annotations

from typing import Any

from app import indicators

BREAKOUT_PERIOD = 20
MIN_PULLBACK = 2
MAX_EXTREME_AGO = 4  # 2-day pullback + rebreak within ~3 days of the extreme


def _empty_breakout() -> dict[str, Any]:
    return {
        "period": BREAKOUT_PERIOD,
        "high_20": None,
        "low_20": None,
        "side": None,
        "extreme_bars_ago": None,
        "pullback_bars": None,
        "rebreak": False,
    }


def perfect_order_series(
    sma_series_map: dict[int, list[float | None]],
) -> list[str | None]:
    """Per-bar ``up`` / ``down`` / ``None`` from aligned SMA series."""
    if not sma_series_map:
        return []
    n = len(next(iter(sma_series_map.values())))
    out: list[str | None] = []
    for i in range(n):
        sma = {p: sma_series_map[p][i] for p in indicators.SMA_PERIODS}
        out.append(indicators.perfect_order(sma))
    return out


def perfect_order_age(order_series: list[str | None]) -> int | None:
    """Consecutive bars at the end that share the current stack direction."""
    if not order_series:
        return None
    side = order_series[-1]
    if side not in ("up", "down"):
        return None
    age = 0
    for order in reversed(order_series):
        if order == side:
            age += 1
        else:
            break
    return age


def prior_day_high_low(bars: list[dict]) -> dict[str, float | None]:
    """High/low of the bar before the last (prior daily session when ``bars`` are D)."""
    if len(bars) < 2:
        return {"high": None, "low": None}
    prev = bars[-2]
    return {"high": float(prev["high"]), "low": float(prev["low"])}


def _tagged_high(highs: list[float], j: int, period: int) -> bool:
    if j < period:
        return False
    level = max(highs[j - period : j])
    return highs[j] >= level


def _tagged_low(lows: list[float], j: int, period: int) -> bool:
    if j < period:
        return False
    level = min(lows[j - period : j])
    return lows[j] <= level


def breakout_20_state(
    bars: list[dict],
    period: int = BREAKOUT_PERIOD,
) -> dict[str, Any]:
    """20-day extreme → ≥2-day pullback → rebreak (not first touch).

    Levels use the 20 bars *before* the last bar. A long rebreak is a close
    above that high after a prior tag and at least two intervening pullback
    bars; short is the mirror. ``rebreak`` is true only on that pulse bar.
    """
    out = _empty_breakout()
    out["period"] = period
    n = len(bars)
    need = period + MIN_PULLBACK + 1
    if n < need:
        return out

    i = n - 1
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    closes = [float(b["close"]) for b in bars]
    high_20 = max(highs[i - period : i])
    low_20 = min(lows[i - period : i])
    out["high_20"] = indicators._round(high_20)  # noqa: SLF001 — shared rounding
    out["low_20"] = indicators._round(low_20)

    def _find_extreme(tag_fn, is_high: bool) -> int | None:
        for j in range(i - 1, i - MAX_EXTREME_AGO - 1, -1):
            if j < period:
                break
            if not tag_fn(j):
                continue
            extreme_level = highs[j] if is_high else lows[j]
            ok = True
            for k in range(j + 1, i):
                if is_high and highs[k] >= extreme_level:
                    ok = False
                    break
                if not is_high and lows[k] <= extreme_level:
                    ok = False
                    break
            if ok:
                return j
        return None

    extreme_long = _find_extreme(lambda j: _tagged_high(highs, j, period), True)
    if extreme_long is not None:
        pullback_long = i - extreme_long - 1
        out["extreme_bars_ago"] = i - extreme_long
        out["pullback_bars"] = pullback_long
        if pullback_long >= MIN_PULLBACK and closes[i] > high_20:
            out["side"] = "long"
            out["rebreak"] = True
            return out

    extreme_short = _find_extreme(lambda j: _tagged_low(lows, j, period), False)
    if extreme_short is not None:
        pullback_short = i - extreme_short - 1
        out["extreme_bars_ago"] = i - extreme_short
        out["pullback_bars"] = pullback_short
        if pullback_short >= MIN_PULLBACK and closes[i] < low_20:
            out["side"] = "short"
            out["rebreak"] = True
            return out

    # First touch of the 20-day on this bar: levels only, do not fire.
    if _tagged_high(highs, i, period):
        out["side"] = "long"
        out["extreme_bars_ago"] = 0
        out["pullback_bars"] = 0
    elif _tagged_low(lows, i, period):
        out["side"] = "short"
        out["extreme_bars_ago"] = 0
        out["pullback_bars"] = 0
    return out
