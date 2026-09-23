"""Deterministic price-structure detectors for regime-change detection.

Pure functions only: no LLM, no network, no I/O. Bars in; structured JSON out.
This module turns the raw swing / trendline geometry in :mod:`app.patterns`
into the structural evidence the regime-change scorer consumes:

* horizontal support / resistance levels (clustered swings + round numbers +
  prior-day high/low),
* support/resistance role reversal (a broken level flips role),
* valid trendline breaks (close + time + price/ATR filters, anti-whipsaw),
* channel state (a failed leg toward the far rail as an early warning;
  basic-rail break),
* the fan principle (successive broken trendlines), and
* swing-structure flips (failed new extreme + prior-swing break).

Book rules stay heuristic and are pinned to the corpus in
``agent/structure_fidelity.py`` (Murphy, Edwards & Magee, Pring, Lien). Volume
judgment belongs to :mod:`app.candles`. Research only; no orders.
"""

from __future__ import annotations

from typing import Any, Literal

from app import patterns, regime_visual

Direction = Literal["up", "down"]

SWING_LEFT = 3
SWING_RIGHT = 3
ATR_PERIOD = 14

# Clustering / touch tolerances as a fraction of ATR.
CLUSTER_ATR_FRAC = 0.5
TOUCH_ATR_FRAC = 0.25
# A decisive break must clear the line by at least this (max of pip floor / ATR).
BREAK_ATR_FRAC = 0.10
BREAK_PIP_FLOOR = 2.0
# Murphy's "two day rule": a close must hold beyond the line for N bars.
TIME_FILTER = 2
# Failure to reach the far channel rail by this fraction of channel width is an
# early warning that the trend is shifting (Murphy 4.17).
FAR_RAIL_FAIL_FRAC = 0.25
# Murphy's warning is a *failed leg*: a rally that previously tagged the return
# line and then falls short of it. A swing whose extreme comes within this
# fraction of the channel width counts as having reached the rail, and only a
# later swing can fail against it. Without the prior-reach requirement the test
# degenerates into "price is not at the rail", which is true of roughly half of
# all bars.
FAR_RAIL_REACH_FRAC = 0.10
# Round-number figures within this many ATRs of the last close are considered.
ROUND_NUMBER_ATR_SPAN = 3.0
FIGURE_PIPS = 100.0  # a "double zero" figure is 100 pips

MAX_LEVELS = 8


class StructureError(ValueError):
    """Raised when inputs violate a structure invariant."""


def _validate_bars(bars: list[dict]) -> None:
    if not bars:
        raise StructureError("bars must be non-empty")
    for i, b in enumerate(bars):
        for key in ("open", "high", "low", "close"):
            if key not in b:
                raise StructureError(f"bar[{i}] missing '{key}'")
        if b["high"] < b["low"]:
            raise StructureError(f"bar[{i}] high < low")


def pip_size(instrument: str | None) -> float:
    """JPY-quoted pairs use 0.01; other FX uses 0.0001. Unknown -> 0.0001."""
    text = (instrument or "").strip().upper().replace("/", "_")
    if not text:
        return 0.0001
    quote = text.split("_")[-1] if "_" in text else text[-3:]
    return 0.01 if quote == "JPY" else 0.0001


def _round(value: float | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _atr(bars: list[dict], period: int = ATR_PERIOD) -> float:
    tol = patterns.atr(bars, period)
    if tol <= 0:
        tol = abs(float(bars[-1]["close"])) * 1e-4 or 1e-4
    return tol


def _line_price_at(line: dict[str, Any], i: int) -> float:
    i0, i1 = int(line["i0"]), int(line["i1"])
    p0, p1 = float(line["price0"]), float(line["price1"])
    if i1 == i0:
        return p0
    slope = (p1 - p0) / (i1 - i0)
    return p0 + slope * (i - i0)


def _rail_price_at(rail: dict[str, Any], i: int) -> float:
    """Interpolate a channel rail (``i1/p1`` .. ``i2/p2``) at bar ``i``."""
    i1, i2 = int(rail["i1"]), int(rail["i2"])
    p1, p2 = float(rail["p1"]), float(rail["p2"])
    if i2 == i1:
        return p1
    slope = (p2 - p1) / (i2 - i1)
    return p1 + slope * (i - i1)


# ---------------------------------------------------------------------------
# Horizontal support / resistance levels
# ---------------------------------------------------------------------------


def _cluster(points: list[dict[str, Any]], tol: float) -> list[dict[str, Any]]:
    """Merge nearby pivots into levels by price proximity (single linkage)."""
    if not points:
        return []
    ordered = sorted(points, key=lambda p: p["price"])
    clusters: list[list[dict[str, Any]]] = [[ordered[0]]]
    for pt in ordered[1:]:
        if abs(pt["price"] - clusters[-1][-1]["price"]) <= tol:
            clusters[-1].append(pt)
        else:
            clusters.append([pt])
    out: list[dict[str, Any]] = []
    for group in clusters:
        prices = [p["price"] for p in group]
        indices = [int(p["index"]) for p in group]
        kinds = sorted({str(p["kind"]) for p in group})
        out.append(
            {
                "price": sum(prices) / len(prices),
                "touches": len(group),
                "pivot_kinds": kinds,
                "first_index": min(indices),
                "last_index": max(indices),
            }
        )
    return out


def horizontal_levels(
    bars: list[dict],
    *,
    instrument: str | None = None,
    pip: float | None = None,
    swing_left: int = SWING_LEFT,
    swing_right: int = SWING_RIGHT,
    cluster_atr_frac: float = CLUSTER_ATR_FRAC,
    max_levels: int = MAX_LEVELS,
) -> dict[str, Any]:
    """Cluster swing highs/lows into S/R levels; add figures + prior-day H/L.

    Each level is tagged ``support`` / ``resistance`` relative to the last close
    (Murphy: a previous low is support, a previous high is resistance, and a
    broken level reverses roles). ``nearest_support`` / ``nearest_resistance``
    give the closest level on each side of price.
    """
    _validate_bars(bars)
    pip = pip if pip is not None else pip_size(instrument)
    atr = _atr(bars)
    tol = cluster_atr_frac * atr
    last_close = float(bars[-1]["close"])
    n = len(bars)

    pivots = patterns.detect_swings(bars, left=swing_left, right=swing_right)
    swing_levels = _cluster(pivots, tol)

    for lvl in swing_levels:
        lvl["source"] = "swing"

    # Round-number figures near price (psychological S/R).
    step = FIGURE_PIPS * pip
    span = ROUND_NUMBER_ATR_SPAN * atr
    figures: list[dict[str, Any]] = []
    if step > 0:
        lo = last_close - span
        hi = last_close + span
        k = int(lo // step)
        price = k * step
        while price <= hi + 1e-12:
            if price > 0 and abs(price - last_close) <= span:
                figures.append(
                    {
                        "price": price,
                        "touches": 0,
                        "pivot_kinds": [],
                        "first_index": n - 1,
                        "last_index": n - 1,
                        "source": "figure",
                    }
                )
            price += step

    prior = _prior_day_levels(bars)

    levels = swing_levels + figures + prior
    # Merge figures/prior into nearby swing clusters (dedupe by tolerance).
    merged = _merge_levels(levels, tol)

    for lvl in merged:
        price = float(lvl["price"])
        if price < last_close:
            lvl["role"] = "support"
        elif price > last_close:
            lvl["role"] = "resistance"
        else:
            lvl["role"] = "at_price"
        lvl["distance"] = _round(abs(price - last_close))
        lvl["distance_pips"] = _round(abs(price - last_close) / pip, 2) if pip else None
        lvl["price"] = _round(price)

    merged.sort(key=lambda x: (-int(x["touches"]), float(x["distance"] or 0.0)))
    merged = merged[:max_levels]

    supports = sorted(
        (x for x in merged if x["role"] == "support"),
        key=lambda x: float(x["distance"] or 0.0),
    )
    resistances = sorted(
        (x for x in merged if x["role"] == "resistance"),
        key=lambda x: float(x["distance"] or 0.0),
    )
    return {
        "last_close": _round(last_close),
        "atr": _round(atr),
        "pip": pip,
        "levels": merged,
        "nearest_support": supports[0] if supports else None,
        "nearest_resistance": resistances[0] if resistances else None,
    }


def _prior_day_levels(bars: list[dict]) -> list[dict[str, Any]]:
    """Prior-bar high/low (a common intraday S/R reference)."""
    if len(bars) < 2:
        return []
    prev = bars[-2]
    n = len(bars)
    return [
        {
            "price": float(prev["high"]),
            "touches": 1,
            "pivot_kinds": ["high"],
            "first_index": n - 2,
            "last_index": n - 2,
            "source": "prior_bar_high",
        },
        {
            "price": float(prev["low"]),
            "touches": 1,
            "pivot_kinds": ["low"],
            "first_index": n - 2,
            "last_index": n - 2,
            "source": "prior_bar_low",
        },
    ]


def _merge_levels(levels: list[dict[str, Any]], tol: float) -> list[dict[str, Any]]:
    """Merge overlapping level candidates from different sources."""
    if not levels:
        return []
    ordered = sorted(levels, key=lambda x: x["price"])
    out: list[dict[str, Any]] = [dict(ordered[0])]
    out[0]["sources"] = [out[0].pop("source", "swing")]
    for lvl in ordered[1:]:
        if abs(float(lvl["price"]) - float(out[-1]["price"])) <= tol:
            prev = out[-1]
            total = prev["touches"] + lvl["touches"]
            if total > 0:
                prev["price"] = (
                    prev["price"] * prev["touches"] + lvl["price"] * lvl["touches"]
                ) / total
            else:
                prev["price"] = (prev["price"] + lvl["price"]) / 2.0
            prev["touches"] = total
            prev["pivot_kinds"] = sorted(
                set(prev.get("pivot_kinds", [])) | set(lvl.get("pivot_kinds", []))
            )
            prev["first_index"] = min(prev["first_index"], lvl["first_index"])
            prev["last_index"] = max(prev["last_index"], lvl["last_index"])
            prev["sources"] = sorted(set(prev["sources"]) | {lvl.get("source", "swing")})
        else:
            new = dict(lvl)
            new["sources"] = [new.pop("source", "swing")]
            out.append(new)
    return out


# ---------------------------------------------------------------------------
# Support / resistance role reversal
# ---------------------------------------------------------------------------


def role_reversal(
    bars: list[dict],
    levels: dict[str, Any] | None = None,
    *,
    instrument: str | None = None,
    pip: float | None = None,
    touch_atr_frac: float = TOUCH_ATR_FRAC,
    break_atr_frac: float = BREAK_ATR_FRAC,
) -> dict[str, Any]:
    """Detect a level that broke and then held its reversed role on a retest.

    Murphy / Edwards & Magee: once a support/resistance level is decisively
    penetrated it reverses roles (old support becomes resistance and vice
    versa). A confirmed reversal is a break followed by a retest that holds.
    """
    _validate_bars(bars)
    pip = pip if pip is not None else pip_size(instrument)
    atr = _atr(bars)
    touch_tol = touch_atr_frac * atr
    break_tol = max(break_atr_frac * atr, BREAK_PIP_FLOOR * pip)
    if levels is None:
        levels = horizontal_levels(bars, instrument=instrument, pip=pip)

    events: list[dict[str, Any]] = []
    n = len(bars)
    for lvl in levels.get("levels", []):
        price = float(lvl["price"])
        start = int(lvl["last_index"])
        broke_up: int | None = None
        broke_down: int | None = None
        for i in range(start + 1, n):
            close = float(bars[i]["close"])
            if broke_up is None and broke_down is None:
                if close > price + break_tol:
                    broke_up = i
                elif close < price - break_tol:
                    broke_down = i
                continue
            # After a break, look for a retest that holds the reversed role.
            if broke_up is not None:
                # old resistance -> support: dip back near the level, close above.
                if float(bars[i]["low"]) <= price + touch_tol and close >= price:
                    events.append(
                        _reversal_event(lvl, price, broke_up, i, "resistance", "support", pip)
                    )
                    break
                if close < price - break_tol:
                    break  # reclaimed; not a clean reversal
            if broke_down is not None:
                # old support -> resistance: rally back near the level, close below.
                if float(bars[i]["high"]) >= price - touch_tol and close <= price:
                    events.append(
                        _reversal_event(lvl, price, broke_down, i, "support", "resistance", pip)
                    )
                    break
                if close > price + break_tol:
                    break
    events.sort(key=lambda e: -int(e["retest_index"]))
    return {
        "count": len(events),
        "events": events,
        "latest": events[0] if events else None,
    }


def _reversal_event(
    lvl: dict[str, Any],
    price: float,
    break_index: int,
    retest_index: int,
    from_role: str,
    to_role: str,
    pip: float,
) -> dict[str, Any]:
    return {
        "price": _round(price),
        "from_role": from_role,
        "to_role": to_role,
        "break_index": int(break_index),
        "retest_index": int(retest_index),
        "touches": int(lvl.get("touches", 0)),
        "sources": lvl.get("sources", []),
        # A resistance->support flip is bullish; support->resistance is bearish.
        "direction": "up" if to_role == "support" else "down",
    }


# ---------------------------------------------------------------------------
# Trendline breaks (close + time + price/ATR filters)
# ---------------------------------------------------------------------------


def trendline_break(
    bars: list[dict],
    lines: list[dict[str, Any]] | None = None,
    *,
    instrument: str | None = None,
    pip: float | None = None,
    time_filter: int = TIME_FILTER,
    break_atr_frac: float = BREAK_ATR_FRAC,
    max_lines: int = 5,
) -> dict[str, Any]:
    """Detect valid breaks of proposed trendlines (anti-whipsaw filters).

    A reversal break is a *support* line broken to the downside or a
    *resistance* line broken to the upside. Validity (Murphy 4.9-4.10;
    Edwards & Magee "Validity of Penetration"): the close must clear the line
    by at least ``break_tol`` for ``time_filter`` consecutive closes. The
    measured-move target projects the pre-break vertical extent past the line.
    """
    _validate_bars(bars)
    pip = pip if pip is not None else pip_size(instrument)
    atr = _atr(bars)
    break_tol = max(break_atr_frac * atr, BREAK_PIP_FLOOR * pip)
    if lines is None:
        pivots = patterns.detect_swings(bars, left=SWING_LEFT, right=SWING_RIGHT)
        lines = patterns.propose_trendlines(pivots, bars, max_lines=max_lines)

    n = len(bars)
    breaks: list[dict[str, Any]] = []
    for line in lines:
        kind = str(line.get("kind"))
        against = "down" if kind == "support" else "up"
        # Count consecutive closing penetrations ending at the last bar.
        consecutive = 0
        first_break_idx: int | None = None
        for i in range(n - 1, -1, -1):
            if i <= int(line["i1"]):
                break
            expected = _line_price_at(line, i)
            close = float(bars[i]["close"])
            beyond = (
                close < expected - break_tol
                if against == "down"
                else close > expected + break_tol
            )
            if beyond:
                consecutive += 1
                first_break_idx = i
            else:
                break
        if consecutive == 0 or first_break_idx is None:
            continue
        expected_last = _line_price_at(line, n - 1)
        close_last = float(bars[-1]["close"])
        penetration = (
            expected_last - close_last if against == "down" else close_last - expected_last
        )
        measured = _trendline_measured_move(bars, line, against)
        breaks.append(
            {
                "kind": kind,
                "break_direction": against,
                "break_index": int(first_break_idx),
                "bars_beyond": consecutive,
                "time_filter_ok": consecutive >= time_filter,
                "penetration": _round(penetration),
                "penetration_pips": _round(penetration / pip, 2) if pip else None,
                "touches": int(line.get("touches", 0)),
                "line": {
                    "i0": int(line["i0"]),
                    "i1": int(line["i1"]),
                    "price0": _round(float(line["price0"])),
                    "price1": _round(float(line["price1"])),
                    "price_at_last": _round(expected_last),
                    "slope": _round(float(line.get("slope", 0.0))),
                },
                "measured_move": measured,
                # A valid break needs both the close filter (implicit) and time.
                "valid": consecutive >= time_filter,
                "direction": "down" if against == "down" else "up",
            }
        )
    breaks.sort(key=lambda b: (-int(b["valid"]), -int(b["bars_beyond"])))
    valid = [b for b in breaks if b["valid"]]
    return {
        "count": len(breaks),
        "valid_count": len(valid),
        "breaks": breaks,
        "latest_valid": valid[0] if valid else None,
    }


def _trendline_measured_move(
    bars: list[dict], line: dict[str, Any], against: str
) -> dict[str, Any] | None:
    """Vertical extent price ran on the trend side, projected past the break."""
    i0 = int(line["i0"])
    n = len(bars)
    height = 0.0
    for i in range(i0, n):
        expected = _line_price_at(line, i)
        if against == "down":
            height = max(height, float(bars[i]["high"]) - expected)
        else:
            height = max(height, expected - float(bars[i]["low"]))
    if height <= 0:
        return None
    line_at_last = _line_price_at(line, n - 1)
    target = line_at_last - height if against == "down" else line_at_last + height
    return {"height": _round(height), "target": _round(target)}


# ---------------------------------------------------------------------------
# Channel state (far-rail failure + basic-rail break)
# ---------------------------------------------------------------------------


def _far_rail_failure_event(
    bars: list[dict],
    far_rail: dict[str, Any],
    direction: Direction,
    width: float,
    *,
    swing_left: int,
    swing_right: int,
    fail_frac: float,
    reach_frac: float,
) -> dict[str, Any] | None:
    """The most recent completed swing that fell short of a previously tagged rail.

    Murphy 4.17 reads the *inability* of a rally to reach the return line, which
    presupposes an earlier rally that did reach it. Returns ``None`` unless both
    legs exist in order, so the warning marks a turning point rather than
    price's resting position inside the channel.
    """
    if width <= 0:
        return None
    want = "high" if direction == "up" else "low"
    pivots = [
        p
        for p in patterns.detect_swings(bars, left=swing_left, right=swing_right)
        if p.get("kind") == want
    ]
    if len(pivots) < 2:
        return None

    legs: list[dict[str, Any]] = []
    for p in pivots:
        idx = int(p["index"])
        rail_price = _rail_price_at(far_rail, idx)
        extreme = float(p["price"])
        gap = rail_price - extreme if direction == "up" else extreme - rail_price
        legs.append({"index": idx, "price": extreme, "gap_frac": gap / width})

    latest = legs[-1]
    # Murphy's warning describes a rally *inside* the channel that fell short.
    # A gap of a full width or more puts the swing at or beyond the basic rail,
    # where the channel is already broken and ``basic_rail_break`` is the
    # applicable signal instead.
    if not fail_frac <= latest["gap_frac"] < 1.0:
        return None
    reached = [lg for lg in legs[:-1] if lg["gap_frac"] <= reach_frac]
    if not reached:
        return None
    prior = reached[-1]
    return {
        "index": latest["index"],
        "price": _round(latest["price"]),
        "gap_frac": _round(latest["gap_frac"], 4),
        "prior_reach_index": prior["index"],
        "prior_reach_price": _round(prior["price"]),
        "prior_reach_gap_frac": _round(prior["gap_frac"], 4),
        "bars_since_reach": latest["index"] - prior["index"],
    }


def channel_state(
    bars: list[dict],
    direction: Direction | None,
    *,
    instrument: str | None = None,
    pip: float | None = None,
    break_atr_frac: float = BREAK_ATR_FRAC,
    far_rail_fail_frac: float = FAR_RAIL_FAIL_FRAC,
    far_rail_reach_frac: float = FAR_RAIL_REACH_FRAC,
    swing_left: int = SWING_LEFT,
    swing_right: int = SWING_RIGHT,
) -> dict[str, Any]:
    """Trend-channel rails + far-rail-failure early warning + basic-rail break.

    Murphy 4.16-4.18: price trends between the basic trendline and a parallel
    return line. A move that fails to reach the far rail is an early warning
    the trend is shifting; a break of the *basic* rail is a trend-change signal
    (measured move = channel width).

    The far-rail warning is a **failed leg**, not a position in the channel: it
    needs a completed swing that tagged the return line, followed by a later
    completed swing that fell short of it. ``far_rail_failure_event`` dates that
    failing swing so the scorer can apply a recency gate.
    """
    _validate_bars(bars)
    pip = pip if pip is not None else pip_size(instrument)
    if direction not in ("up", "down"):
        return {"has_channel": False, "reason": "no directional channel"}
    rails = regime_visual._trend_channel(bars, direction)
    if rails is None:
        return {"has_channel": False, "reason": "insufficient swings for a channel"}

    upper, lower = rails
    atr = _atr(bars)
    break_tol = max(break_atr_frac * atr, BREAK_PIP_FLOOR * pip)
    upper_at_last = float(upper["p2"])
    lower_at_last = float(lower["p2"])
    width = upper_at_last - lower_at_last
    close = float(bars[-1]["close"])

    # Basic rail is the trend-support side; far (return) rail is the other.
    far_rail = upper if direction == "up" else lower
    if direction == "up":
        far_rail_price = upper_at_last
        basic_rail_price = lower_at_last
        basic_break = close < basic_rail_price - break_tol
        far_break = close > far_rail_price + break_tol
    else:
        far_rail_price = lower_at_last
        basic_rail_price = upper_at_last
        basic_break = close > basic_rail_price + break_tol
        far_break = close < far_rail_price - break_tol

    fail_event = _far_rail_failure_event(
        bars,
        far_rail,
        direction,
        width,
        swing_left=swing_left,
        swing_right=swing_right,
        fail_frac=far_rail_fail_frac,
        reach_frac=far_rail_reach_frac,
    )
    far_rail_failure = fail_event is not None
    far_gap_frac = fail_event["gap_frac"] if fail_event else None

    measured_move = None
    if basic_break and width > 0:
        target = (
            basic_rail_price - width if direction == "up" else basic_rail_price + width
        )
        measured_move = {"width": _round(width), "target": _round(target)}

    return {
        "has_channel": True,
        "kind": "trend_channel",
        "direction": direction,
        "upper_at_last": _round(upper_at_last),
        "lower_at_last": _round(lower_at_last),
        "width": _round(width),
        "close": _round(close),
        "far_rail": "upper" if direction == "up" else "lower",
        "far_rail_gap_frac": _round(far_gap_frac, 4) if far_gap_frac is not None else None,
        "far_rail_failure": bool(far_rail_failure),
        "far_rail_failure_event": fail_event,
        "basic_rail_break": bool(basic_break),
        "far_rail_break": bool(far_break),
        "measured_move": measured_move,
        "rails": {"upper": upper, "lower": lower},
        # Basic-rail break reverses the trend.
        "reversal_direction": ("down" if direction == "up" else "up") if basic_break else None,
    }


# ---------------------------------------------------------------------------
# Fan principle (successive broken trendlines)
# ---------------------------------------------------------------------------


def fan_state(
    bars: list[dict],
    direction: Direction | None = None,
    *,
    instrument: str | None = None,
    pip: float | None = None,
    max_lines: int = 8,
) -> dict[str, Any]:
    """Count successive broken same-side trendlines (fan principle).

    Murphy 4.11: as a trend weakens the analyst draws a second, then a third,
    flatter trendline; breaking the third usually signals the move. In an
    uptrend the relevant lines are rising *support* lines; in a downtrend they
    are falling *resistance* lines.
    """
    _validate_bars(bars)
    pip = pip if pip is not None else pip_size(instrument)
    pivots = patterns.detect_swings(bars, left=SWING_LEFT, right=SWING_RIGHT)
    lines = patterns.propose_trendlines(pivots, bars, max_lines=max_lines)
    if direction == "up":
        want = "support"
    elif direction == "down":
        want = "resistance"
    else:
        want = None
    candidate_lines = [ln for ln in lines if want is None or ln.get("kind") == want]
    result = trendline_break(bars, candidate_lines, instrument=instrument, pip=pip)
    broken = [b for b in result["breaks"] if b["valid"]]
    broken.sort(key=lambda b: int(b["break_index"]))
    return {
        "direction": direction,
        "line_count": len(candidate_lines),
        "broken_count": len(broken),
        "first_line_broken": len(broken) >= 1,
        "third_line_broken": len(broken) >= 3,
        "broken": broken,
    }


# ---------------------------------------------------------------------------
# Swing-structure flip (Dow: failed new extreme + prior-swing break)
# ---------------------------------------------------------------------------


def _compress_pivots(pivots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge runs of same-kind pivots (ties on adjacent bars) into one extreme."""
    out: list[dict[str, Any]] = []
    for p in pivots:
        if out and out[-1]["kind"] == p["kind"]:
            prev = out[-1]
            if (p["kind"] == "high" and p["price"] >= prev["price"]) or (
                p["kind"] == "low" and p["price"] <= prev["price"]
            ):
                out[-1] = p
            continue
        out.append(p)
    return out


def swing_structure_flip(
    bars: list[dict],
    *,
    swing_left: int = SWING_LEFT,
    swing_right: int = SWING_RIGHT,
) -> dict[str, Any]:
    """Failed new high/low then a break of the prior swing (trend-change).

    Edwards & Magee / Dow: an uptrend is higher highs and higher lows. When a
    rally fails to make a new high and price then sifts down through the prior
    swing low, that break is the first step of a reversal (mirror for
    downtrends). The prior trend is read from the last three swings so a lower
    high after an up-leg is distinguishable from the up-leg itself.
    """
    _validate_bars(bars)
    pivots = _compress_pivots(
        patterns.detect_swings(bars, left=swing_left, right=swing_right)
    )
    highs = [p for p in pivots if p["kind"] == "high"]
    lows = [p for p in pivots if p["kind"] == "low"]
    empty = {
        "prior_trend": None,
        "failed_new_extreme": False,
        "broke_prior_swing": False,
        "flip": False,
        "direction_to": None,
    }
    if len(highs) < 3 or len(lows) < 3:
        return empty

    last_close = float(bars[-1]["close"])
    hh = [float(h["price"]) for h in highs]
    ll = [float(l["price"]) for l in lows]

    up_context = hh[-2] > hh[-3] and ll[-2] > ll[-3]
    down_context = hh[-2] < hh[-3] and ll[-2] < ll[-3]

    if up_context:
        failed = hh[-1] <= hh[-2]
        broke = last_close < ll[-1]
        flip = failed and broke
        return {
            "prior_trend": "up",
            "failed_new_extreme": bool(failed),
            "broke_prior_swing": bool(broke),
            "flip": bool(flip),
            "direction_to": "down" if broke else None,
            "prior_swing_low": _round(ll[-1]),
        }
    if down_context:
        failed = ll[-1] >= ll[-2]
        broke = last_close > hh[-1]
        flip = failed and broke
        return {
            "prior_trend": "down",
            "failed_new_extreme": bool(failed),
            "broke_prior_swing": bool(broke),
            "flip": bool(flip),
            "direction_to": "up" if broke else None,
            "prior_swing_high": _round(hh[-1]),
        }
    return empty


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


def analyze_structure(
    bars: list[dict],
    direction: Direction | None = None,
    *,
    instrument: str | None = None,
) -> dict[str, Any]:
    """Run every structural detector on ``bars`` and return one payload."""
    _validate_bars(bars)
    pip = pip_size(instrument)
    levels = horizontal_levels(bars, instrument=instrument, pip=pip)
    return {
        "bar_count": len(bars),
        "last_close": _round(float(bars[-1]["close"])),
        "last_time": bars[-1].get("time"),
        "direction": direction,
        "pip": pip,
        "levels": levels,
        "role_reversal": role_reversal(bars, levels, instrument=instrument, pip=pip),
        "trendline_break": trendline_break(bars, instrument=instrument, pip=pip),
        "channel": channel_state(bars, direction, instrument=instrument, pip=pip),
        "fan": fan_state(bars, direction, instrument=instrument, pip=pip),
        "swing_flip": swing_structure_flip(bars),
    }
