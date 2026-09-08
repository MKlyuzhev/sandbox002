"""Compact Lien regime channel rails (display only).

Trend: swing trendline + parallel (Ch.15 construction as overlay, not an engine).
Range / mixed: last 10-bar high/low as a time-bounded box (Ch.7).
Oscillators stay out. No Ch.15 ±10-pip entries.
"""

from __future__ import annotations

from typing import Any

from app.indicators import HIGH_N
from app.patterns import detect_swings, propose_trendlines

CITE = {"source": "lien-fx", "chunks": [75, 91]}
NOTES = "display only; not a Ch.15 entry"
SWING_WINDOW = 80
PRICE_DIGITS = 6


def _price_at(i0: int, p0: float, i1: int, p1: float, i: int) -> float:
    if i1 == i0:
        return p0
    slope = (p1 - p0) / (i1 - i0)
    return p0 + slope * (i - i0)


def _round_px(value: float) -> float:
    return round(float(value), PRICE_DIGITS)


def _rail(bars: list[dict], i1: int, p1: float, i2: int, p2: float) -> dict[str, Any]:
    return {
        "t1": bars[i1].get("time"),
        "p1": _round_px(p1),
        "t2": bars[i2].get("time"),
        "p2": _round_px(p2),
        "i1": int(i1),
        "i2": int(i2),
    }


def _payload(
    kind: str,
    upper: dict[str, Any],
    lower: dict[str, Any],
    extra_notes: str | None = None,
) -> dict[str, Any]:
    notes = NOTES if extra_notes is None else f"{NOTES}; {extra_notes}"
    return {
        "kind": kind,
        "cite": dict(CITE),
        "upper": upper,
        "lower": lower,
        "notes": notes,
    }


def _nbar_box(bars: list[dict], n: int = HIGH_N) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if not bars:
        return None
    window = bars[-n:] if len(bars) >= n else bars
    i_left = len(bars) - len(window)
    i_right = len(bars) - 1
    if i_left >= i_right:
        return None
    hi = max(float(b["high"]) for b in window)
    lo = min(float(b["low"]) for b in window)
    return _rail(bars, i_left, hi, i_right, hi), _rail(bars, i_left, lo, i_right, lo)


def _trend_channel(
    bars: list[dict],
    direction: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    start = max(0, len(bars) - SWING_WINDOW)
    window = bars[start:]
    pivots = detect_swings(window)
    for pivot in pivots:
        pivot["index"] = int(pivot["index"]) + start
    if len(pivots) < 2:
        return None
    want = "support" if direction == "up" else "resistance"
    lines = [
        ln
        for ln in propose_trendlines(pivots, bars, max_lines=8)
        if ln.get("kind") == want
    ]
    if not lines:
        return None
    primary = lines[0]
    i0, i1 = int(primary["i0"]), int(primary["i1"])
    p0, p1 = float(primary["price0"]), float(primary["price1"])
    if i0 == i1:
        return None
    last_i = len(bars) - 1
    slope = (p1 - p0) / (i1 - i0)
    ext_from = min(i0, start)
    if direction == "up":
        ext_i = max(range(ext_from, len(bars)), key=lambda i: float(bars[i]["high"]))
        ext_p = float(bars[ext_i]["high"])
    else:
        ext_i = min(range(ext_from, len(bars)), key=lambda i: float(bars[i]["low"]))
        ext_p = float(bars[ext_i]["low"])
    i_left = min(i0, ext_i)
    if i_left >= last_i:
        return None
    primary_left = _price_at(i0, p0, i1, p1, i_left)
    primary_right = _price_at(i0, p0, i1, p1, last_i)
    parallel_left = ext_p + slope * (i_left - ext_i)
    parallel_right = ext_p + slope * (last_i - ext_i)
    if direction == "up":
        lower = _rail(bars, i_left, primary_left, last_i, primary_right)
        upper = _rail(bars, i_left, parallel_left, last_i, parallel_right)
    else:
        upper = _rail(bars, i_left, primary_left, last_i, primary_right)
        lower = _rail(bars, i_left, parallel_left, last_i, parallel_right)
    if upper["p1"] < lower["p1"] and upper["p2"] < lower["p2"]:
        upper, lower = lower, upper
    return upper, lower


def channel_geometry(analysis: dict[str, Any], bars: list[dict]) -> dict[str, Any] | None:
    """Two rails for classify_regime JSON and mt4_draw_regime. Display only."""
    if not bars or len(bars) < 2:
        return None
    regime = analysis.get("regime")
    direction = analysis.get("direction")
    if regime == "trend" and direction in ("up", "down"):
        rails = _trend_channel(bars, direction)
        if rails is not None:
            return _payload("trend_channel", rails[0], rails[1])
        box = _nbar_box(bars)
        if box is None:
            return None
        return _payload(
            "trend_channel",
            box[0],
            box[1],
            extra_notes="fallback 10-bar high/low (thin swings)",
        )
    box = _nbar_box(bars)
    if box is None:
        return None
    return _payload("range_channel", box[0], box[1])
