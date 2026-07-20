"""Deterministic swing / trendline / early H&S geometry.

Pure functions only: no LLM, no network, no I/O. Bars in; structured stage
JSON out. Volume judgment and book rules belong to the corpus + local model
(see docs/FORMATION_ANALYSIS.md).
"""

from __future__ import annotations

from typing import Any, Literal

Kind = Literal["high", "low"]
LineKind = Literal["support", "resistance"]
HsStage = Literal[
    "none",
    "left_shoulder",
    "head",
    "right_shoulder_forming",
    "neckline_tentative",
    "confirmed_break",
    "invalidated",
]


class PatternError(ValueError):
    """Raised when inputs violate a pattern invariant."""


def _validate_bars(bars: list[dict]) -> None:
    if not bars:
        raise PatternError("bars must be non-empty")
    for i, b in enumerate(bars):
        for key in ("open", "high", "low", "close"):
            if key not in b:
                raise PatternError(f"bar[{i}] missing '{key}'")
        if b["high"] < b["low"]:
            raise PatternError(f"bar[{i}] high < low")


def atr(bars: list[dict], period: int = 14) -> float:
    """Average True Range over the last ``period`` bars (Wilder-style simple mean)."""
    _validate_bars(bars)
    if len(bars) < 2:
        return abs(bars[-1]["high"] - bars[-1]["low"])
    n = min(period, len(bars) - 1)
    trs: list[float] = []
    start = len(bars) - n
    for i in range(start, len(bars)):
        h, l, prev_c = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
    return sum(trs) / len(trs) if trs else 0.0


def detect_swings(
    bars: list[dict],
    left: int = 3,
    right: int = 3,
) -> list[dict[str, Any]]:
    """Return swing highs/lows using a simple fractal (left/right neighbor) rule.

    A swing high at index i requires high[i] >= high[j] for all j in
    [i-left, i+right] excluding i (ties allowed). Same for lows with ``low``.
    """
    _validate_bars(bars)
    if left < 1 or right < 1:
        raise PatternError("left and right must be >= 1")
    pivots: list[dict[str, Any]] = []
    n = len(bars)
    for i in range(left, n - right):
        h = bars[i]["high"]
        l = bars[i]["low"]
        is_high = all(
            h >= bars[j]["high"] for j in range(i - left, i + right + 1) if j != i
        )
        is_low = all(
            l <= bars[j]["low"] for j in range(i - left, i + right + 1) if j != i
        )
        if is_high:
            pivots.append(
                {
                    "index": i,
                    "time": bars[i].get("time"),
                    "price": float(h),
                    "kind": "high",
                }
            )
        if is_low:
            pivots.append(
                {
                    "index": i,
                    "time": bars[i].get("time"),
                    "price": float(l),
                    "kind": "low",
                }
            )
    pivots.sort(key=lambda p: (p["index"], 0 if p["kind"] == "high" else 1))
    return pivots


def _line_price_at(i0: int, p0: float, i1: int, p1: float, i: int) -> float:
    if i1 == i0:
        return p0
    slope = (p1 - p0) / (i1 - i0)
    return p0 + slope * (i - i0)


def propose_trendlines(
    pivots: list[dict],
    bars: list[dict],
    max_lines: int = 5,
    atr_period: int = 14,
    touch_atr_frac: float = 0.15,
) -> list[dict[str, Any]]:
    """Propose trendlines from consecutive same-kind swings; score by touches.

    Tolerance for a touch is ``touch_atr_frac * ATR``. Returns up to ``max_lines``
    candidates sorted by touch count then recency.
    """
    _validate_bars(bars)
    if max_lines < 1:
        raise PatternError("max_lines must be >= 1")
    tol = touch_atr_frac * atr(bars, atr_period)
    if tol <= 0:
        tol = abs(bars[-1]["close"]) * 1e-4

    candidates: list[dict[str, Any]] = []
    for kind in ("high", "low"):
        same = [p for p in pivots if p["kind"] == kind]
        for a in range(len(same) - 1):
            p0, p1 = same[a], same[a + 1]
            i0, i1 = p0["index"], p1["index"]
            price0, price1 = p0["price"], p1["price"]
            touches = 0
            for p in same:
                expected = _line_price_at(i0, price0, i1, price1, p["index"])
                if abs(p["price"] - expected) <= tol:
                    touches += 1
            last_i = len(bars) - 1
            price_at_last = _line_price_at(i0, price0, i1, price1, last_i)
            slope = (price1 - price0) / (i1 - i0) if i1 != i0 else 0.0
            line_kind: LineKind = "resistance" if kind == "high" else "support"
            candidates.append(
                {
                    "kind": line_kind,
                    "pivot_kind": kind,
                    "i0": i0,
                    "i1": i1,
                    "price0": price0,
                    "price1": price1,
                    "slope": slope,
                    "price_at_last": price_at_last,
                    "touches": touches,
                    "tolerance": tol,
                }
            )

    candidates.sort(key=lambda c: (-c["touches"], -c["i1"]))
    return candidates[:max_lines]


def _neckline_price(low_a: dict, low_b: dict, index: int) -> float:
    return _line_price_at(
        low_a["index"], low_a["price"], low_b["index"], low_b["price"], index
    )


def hs_formation_state(
    pivots: list[dict],
    bars: list[dict],
    break_frac: float = 0.001,
) -> dict[str, Any]:
    """Objective early head-and-shoulders **top** detector.

    Stages:
      none | left_shoulder | head | right_shoulder_forming |
      neckline_tentative | confirmed_break | invalidated

    Confirmation: last close below the neckline by ``break_frac`` of price
    (FX-oriented; Edwards' ~3% equity rule is not used here).

    Volume fields are always ``null`` when unavailable — do not invent volume rules.
    """
    _validate_bars(bars)
    if break_frac < 0:
        raise PatternError("break_frac must be >= 0")

    empty = {
        "pattern": "head_and_shoulders_top",
        "stage": "none",
        "left_shoulder": None,
        "head": None,
        "right_shoulder": None,
        "neckline": None,
        "height": None,
        "min_target": None,
        "last_close": float(bars[-1]["close"]),
        "break_frac": break_frac,
        "volume": None,
        "notes": [],
    }

    highs = [p for p in pivots if p["kind"] == "high"]
    lows = [p for p in pivots if p["kind"] == "low"]
    if len(highs) < 1:
        return empty

    # Progressive: at least one high → left_shoulder
    if len(highs) == 1:
        out = dict(empty)
        out["stage"] = "left_shoulder"
        out["left_shoulder"] = highs[0]
        out["notes"] = ["Single swing high; waiting for a higher head."]
        return out

    # Find latest L-H-R triple among highs: H highest, L and R lower
    best: tuple[dict, dict, dict] | None = None
    for i in range(len(highs) - 2):
        ls, hd, rs = highs[i], highs[i + 1], highs[i + 2]
        if hd["price"] > ls["price"] and hd["price"] > rs["price"]:
            best = (ls, hd, rs)

    if best is None:
        # Two+ highs but no head yet: last high vs previous
        out = dict(empty)
        if highs[-1]["price"] > highs[-2]["price"]:
            out["stage"] = "head"
            out["left_shoulder"] = highs[-2]
            out["head"] = highs[-1]
            out["notes"] = ["Higher high after left shoulder; waiting for right shoulder."]
        else:
            out["stage"] = "left_shoulder"
            out["left_shoulder"] = highs[-1]
            out["notes"] = ["No L-H-R structure yet."]
        return out

    ls, hd, rs = best
    out = dict(empty)
    out["left_shoulder"] = ls
    out["head"] = hd
    out["right_shoulder"] = rs

    # Neckline: last swing low between LS and head, and between head and RS
    lows_lh = [p for p in lows if ls["index"] < p["index"] < hd["index"]]
    lows_hr = [p for p in lows if hd["index"] < p["index"] < rs["index"]]
    if not lows_lh or not lows_hr:
        out["stage"] = "right_shoulder_forming"
        out["notes"] = [
            "L-H-R highs present but intervening swing lows incomplete for neckline."
        ]
        return out

    n_low_a = lows_lh[-1]
    n_low_b = lows_hr[-1]
    last_i = len(bars) - 1
    nl_at_last = _neckline_price(n_low_a, n_low_b, last_i)
    height = hd["price"] - _neckline_price(n_low_a, n_low_b, hd["index"])
    out["neckline"] = {
        "i0": n_low_a["index"],
        "i1": n_low_b["index"],
        "price0": n_low_a["price"],
        "price1": n_low_b["price"],
        "price_at_last": nl_at_last,
    }
    out["height"] = height if height > 0 else None

    close = float(bars[-1]["close"])
    threshold = nl_at_last * (1.0 - break_frac)

    # Invalidate if a new high above the head appears after RS
    later_highs = [p for p in highs if p["index"] > rs["index"] and p["price"] > hd["price"]]
    if later_highs:
        out["stage"] = "invalidated"
        out["notes"] = ["New high above the head after right shoulder."]
        return out

    if close < threshold:
        out["stage"] = "confirmed_break"
        out["min_target"] = (nl_at_last - height) if height and height > 0 else None
        out["notes"] = [
            f"Close {close} below neckline {nl_at_last} by break_frac={break_frac}."
        ]
        return out

    out["stage"] = "neckline_tentative"
    out["notes"] = [
        "Tentative neckline drawn from intervening swing lows; awaiting close break."
    ]
    return out


def analyze_bars(
    bars: list[dict],
    swing_left: int = 3,
    swing_right: int = 3,
    max_lines: int = 5,
    break_frac: float = 0.001,
) -> dict[str, Any]:
    """Run swings + trendlines + H&S top state on a bar series."""
    pivots = detect_swings(bars, left=swing_left, right=swing_right)
    lines = propose_trendlines(pivots, bars, max_lines=max_lines)
    hs = hs_formation_state(pivots, bars, break_frac=break_frac)
    return {
        "bar_count": len(bars),
        "last_close": float(bars[-1]["close"]),
        "last_time": bars[-1].get("time"),
        "swing_count": len(pivots),
        "swings": pivots[-20:],  # summary: most recent
        "trendlines": lines,
        "hs": hs,
    }
