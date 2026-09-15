"""Deterministic OHLCV candlestick / reversal-pattern detectors.

Pure functions only: no LLM, no network, no I/O. Bars in; structured JSON out.
These are the *secondary* confirmations for regime-change detection: single- and
two-bar reversal patterns (Nison), plus the Western key reversal day (Edwards &
Magee). Each detector reports the reversal ``direction`` and, where relevant,
whether the pattern sits at a structural level and whether tick volume confirms
it.

FX has no true traded volume, so ``volume_kind`` is ``tick`` when OANDA tick
counts are present and ``unavailable`` otherwise. A pattern is more significant
at support/resistance and when volume expands (Nison ch.15; Edwards & Magee).
Research only; no orders.
"""

from __future__ import annotations

from typing import Any

DOJI_BODY_FRAC = 0.1  # body <= 10% of range is a doji
LONG_SHADOW_FRAC = 2.0  # shadow >= 2x body for hammer / shooting star
SMALL_BODY_FRAC = 0.35  # opposite shadow small relative to range
VOLUME_CONFIRM_FACTOR = 1.2  # tick volume vs trailing average
VOLUME_LOOKBACK = 10
LEVEL_TOUCH_ATR_FRAC = 0.25


class CandleError(ValueError):
    """Raised when inputs violate a candle invariant."""


def _validate_bars(bars: list[dict]) -> None:
    if not bars:
        raise CandleError("bars must be non-empty")
    for i, b in enumerate(bars):
        for key in ("open", "high", "low", "close"):
            if key not in b:
                raise CandleError(f"bar[{i}] missing '{key}'")
        if b["high"] < b["low"]:
            raise CandleError(f"bar[{i}] high < low")


def _o(bar: dict) -> float:
    return float(bar["open"])


def _h(bar: dict) -> float:
    return float(bar["high"])


def _l(bar: dict) -> float:
    return float(bar["low"])


def _c(bar: dict) -> float:
    return float(bar["close"])


def _body(bar: dict) -> float:
    return abs(_c(bar) - _o(bar))


def _rng(bar: dict) -> float:
    return _h(bar) - _l(bar)


def _upper_shadow(bar: dict) -> float:
    return _h(bar) - max(_o(bar), _c(bar))


def _lower_shadow(bar: dict) -> float:
    return min(_o(bar), _c(bar)) - _l(bar)


def _is_white(bar: dict) -> bool:
    return _c(bar) > _o(bar)


def _is_black(bar: dict) -> bool:
    return _c(bar) < _o(bar)


def _round(value: float | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _idx(bars: list[dict], index: int | None) -> int:
    i = len(bars) - 1 if index is None else index
    if i < 0:
        i += len(bars)
    if not 0 <= i < len(bars):
        raise CandleError(f"index {index} out of range for {len(bars)} bars")
    return i


# ---------------------------------------------------------------------------
# Individual patterns (return None or a dict with a reversal ``direction``)
# ---------------------------------------------------------------------------


def engulfing(bars: list[dict], index: int | None = None) -> dict[str, Any] | None:
    """Bullish/bearish engulfing: current real body engulfs the prior body."""
    _validate_bars(bars)
    i = _idx(bars, index)
    if i < 1:
        return None
    prev, cur = bars[i - 1], bars[i]
    prev_top = max(_o(prev), _c(prev))
    prev_bot = min(_o(prev), _c(prev))
    cur_top = max(_o(cur), _c(cur))
    cur_bot = min(_o(cur), _c(cur))
    engulfs = cur_top >= prev_top and cur_bot <= prev_bot and _body(cur) > _body(prev)
    if not engulfs:
        return None
    if _is_white(cur) and not _is_white(prev):
        return {"pattern": "bullish_engulfing", "direction": "up", "index": i}
    if _is_black(cur) and not _is_black(prev):
        return {"pattern": "bearish_engulfing", "direction": "down", "index": i}
    return None


def doji(bars: list[dict], index: int | None = None) -> dict[str, Any] | None:
    """Doji (indecision / transition). Sub-type from shadow geometry."""
    _validate_bars(bars)
    i = _idx(bars, index)
    bar = bars[i]
    rng = _rng(bar)
    if rng <= 0:
        return None
    if _body(bar) > DOJI_BODY_FRAC * rng:
        return None
    up = _upper_shadow(bar)
    lo = _lower_shadow(bar)
    if lo <= 0.25 * rng and up >= 0.5 * rng:
        kind, direction = "gravestone_doji", "down"
    elif up <= 0.25 * rng and lo >= 0.5 * rng:
        kind, direction = "dragonfly_doji", "up"
    elif up >= 0.3 * rng and lo >= 0.3 * rng:
        kind, direction = "long_legged_doji", None
    else:
        kind, direction = "doji", None
    return {"pattern": kind, "direction": direction, "index": i}


def hammer_shooting_star(
    bars: list[dict], index: int | None = None
) -> dict[str, Any] | None:
    """Hammer (bullish) / shooting star (bearish): small body, one long shadow."""
    _validate_bars(bars)
    i = _idx(bars, index)
    bar = bars[i]
    rng = _rng(bar)
    body = _body(bar)
    if rng <= 0 or body <= 0:
        return None
    up = _upper_shadow(bar)
    lo = _lower_shadow(bar)
    if lo >= LONG_SHADOW_FRAC * body and up <= SMALL_BODY_FRAC * rng:
        return {"pattern": "hammer", "direction": "up", "index": i}
    if up >= LONG_SHADOW_FRAC * body and lo <= SMALL_BODY_FRAC * rng:
        return {"pattern": "shooting_star", "direction": "down", "index": i}
    return None


def dark_cloud_piercing(
    bars: list[dict], index: int | None = None
) -> dict[str, Any] | None:
    """Dark-cloud cover (bearish) / piercing pattern (bullish)."""
    _validate_bars(bars)
    i = _idx(bars, index)
    if i < 1:
        return None
    prev, cur = bars[i - 1], bars[i]
    prev_mid = (_o(prev) + _c(prev)) / 2.0
    # Dark-cloud cover: after a white candle, open above its high, close well
    # into (below the midpoint of) the white body.
    if _is_white(prev) and _is_black(cur):
        if _o(cur) > _h(prev) and prev_mid > _c(cur) > _o(prev):
            return {"pattern": "dark_cloud_cover", "direction": "down", "index": i}
    # Piercing pattern: after a black candle, open below its low, close above
    # its midpoint.
    if _is_black(prev) and _is_white(cur):
        if _o(cur) < _l(prev) and prev_mid < _c(cur) < _o(prev):
            return {"pattern": "piercing", "direction": "up", "index": i}
    return None


def harami(bars: list[dict], index: int | None = None) -> dict[str, Any] | None:
    """Harami: a small real body contained within the prior larger body."""
    _validate_bars(bars)
    i = _idx(bars, index)
    if i < 1:
        return None
    prev, cur = bars[i - 1], bars[i]
    prev_top = max(_o(prev), _c(prev))
    prev_bot = min(_o(prev), _c(prev))
    cur_top = max(_o(cur), _c(cur))
    cur_bot = min(_o(cur), _c(cur))
    contained = cur_top <= prev_top and cur_bot >= prev_bot and _body(cur) < _body(prev)
    if not contained:
        return None
    if _is_black(prev) and _is_white(cur):
        return {"pattern": "bullish_harami", "direction": "up", "index": i}
    if _is_white(prev) and _is_black(cur):
        return {"pattern": "bearish_harami", "direction": "down", "index": i}
    return None


def key_reversal_day(
    bars: list[dict], index: int | None = None
) -> dict[str, Any] | None:
    """Western key reversal day (Edwards & Magee).

    Bullish: a new low versus the prior bar, then a close above the prior close.
    Bearish: a new high, then a close below the prior close.
    """
    _validate_bars(bars)
    i = _idx(bars, index)
    if i < 1:
        return None
    prev, cur = bars[i - 1], bars[i]
    if _l(cur) < _l(prev) and _c(cur) > _c(prev):
        return {"pattern": "bullish_key_reversal", "direction": "up", "index": i}
    if _h(cur) > _h(prev) and _c(cur) < _c(prev):
        return {"pattern": "bearish_key_reversal", "direction": "down", "index": i}
    return None


_DETECTORS = (
    engulfing,
    doji,
    hammer_shooting_star,
    dark_cloud_piercing,
    harami,
    key_reversal_day,
)


# ---------------------------------------------------------------------------
# Volume + level context
# ---------------------------------------------------------------------------


def volume_context(
    bars: list[dict],
    index: int | None = None,
    *,
    lookback: int = VOLUME_LOOKBACK,
    factor: float = VOLUME_CONFIRM_FACTOR,
) -> dict[str, Any]:
    """Tick-volume expansion vs the trailing average. FX has no true volume."""
    _validate_bars(bars)
    i = _idx(bars, index)
    vol = bars[i].get("volume")
    if vol is None:
        return {"volume_kind": "unavailable", "volume_confirmed": None}
    prior = [b.get("volume") for b in bars[max(0, i - lookback):i]]
    prior_vals = [float(v) for v in prior if v is not None]
    if not prior_vals:
        return {
            "volume_kind": "tick",
            "volume": float(vol),
            "volume_confirmed": None,
            "avg_volume": None,
        }
    avg = sum(prior_vals) / len(prior_vals)
    return {
        "volume_kind": "tick",
        "volume": float(vol),
        "avg_volume": _round(avg, 2),
        "volume_confirmed": bool(float(vol) >= factor * avg),
    }


def _pattern_pivot(bars: list[dict], pat: dict[str, Any]) -> float:
    """The price that should sit at a level: low for bullish, high for bearish."""
    bar = bars[int(pat["index"])]
    if pat.get("direction") == "up":
        return _l(bar)
    if pat.get("direction") == "down":
        return _h(bar)
    return _c(bar)


def _at_level(
    bars: list[dict],
    pat: dict[str, Any],
    levels: dict[str, Any] | None,
) -> dict[str, Any]:
    if not levels:
        return {"at_level": None}
    atr = float(levels.get("atr") or 0.0)
    tol = LEVEL_TOUCH_ATR_FRAC * atr if atr > 0 else None
    price = _pattern_pivot(bars, pat)
    best: dict[str, Any] | None = None
    best_dist = None
    for lvl in levels.get("levels", []):
        dist = abs(price - float(lvl["price"]))
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best = lvl
    if best is None or tol is None:
        return {"at_level": None}
    return {
        "at_level": bool(best_dist is not None and best_dist <= tol),
        "level_price": best["price"],
        "level_role": best.get("role"),
        "level_distance": _round(best_dist),
    }


def analyze_candles(
    bars: list[dict],
    index: int | None = None,
    *,
    levels: dict[str, Any] | None = None,
    volume_lookback: int = VOLUME_LOOKBACK,
) -> dict[str, Any]:
    """Detect every reversal pattern at ``index`` with volume + level context."""
    _validate_bars(bars)
    i = _idx(bars, index)
    vol = volume_context(bars, i, lookback=volume_lookback)
    patterns_found: list[dict[str, Any]] = []
    for detector in _DETECTORS:
        found = detector(bars, i)
        if found is None:
            continue
        found.update(_at_level(bars, found, levels))
        found["volume_kind"] = vol["volume_kind"]
        found["volume_confirmed"] = vol["volume_confirmed"]
        patterns_found.append(found)

    up = [p for p in patterns_found if p.get("direction") == "up"]
    down = [p for p in patterns_found if p.get("direction") == "down"]
    if up and not down:
        net = "up"
    elif down and not up:
        net = "down"
    else:
        net = None
    return {
        "index": i,
        "last_time": bars[i].get("time"),
        "patterns": patterns_found,
        "reversal_direction": net,
        "any_at_level": any(p.get("at_level") for p in patterns_found),
        "any_volume_confirmed": any(
            p.get("volume_confirmed") for p in patterns_found
        ),
        "volume": vol,
    }
