"""Channel grid for the multi-timeframe read (docs/MTF_CHANNEL_ANALYSIS.md).

Step 1 is W1, step 2 is D1 (``TFS``). Five parallel lines: the trend channel's two rails, the
center line, and two "air" boundaries ``air`` (20%) of the width in from each
rail. Everything here is code-owned: the planner calls it and reports the JSON;
it does not pick anchors or compute zones in prose.

- ``load_bars`` reads the bars MT4 draws (``history/<server>/<SYM><period>.hst``)
  and appends newer OANDA bars (bid, MT4 stamps).
- ``propose_channel`` finds the trend and ranks candidate channels.
- ``grid_from_proposal`` / ``grid_from_chart`` turn a candidate, or the
  trader's own lines in the EA's ``chart.json``, into a ``Grid``.
- ``read_grid`` measures zones, air visits and rail tests by bar close.
- ``grid_objects`` gives the five lines as editable bridge objects.
- ``w1_context`` is the W1 read a lower-timeframe read is reported against.

Lines are evaluated by **bar index**, the way MT4 draws them. Research only;
nothing here places, modifies, or closes orders.
"""

from __future__ import annotations

import bisect
import time as _time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import mt4_bridge
from app.mt4_hst import W1_STAMP_SHIFT, broker_ts, broker_ts_from_utc, read_hst

WEEK = 604800
DEFAULT_SERVER = "OANDA-Demo-1"
AIR = 0.20
TOUCH_TOL = 0.003  # a bar's range within 0.3% of a line
VISIT_GAP = 2  # weeks of other zones merged inside one visit
TOUCH_GAP = 3  # weeks between touches merged into one episode
PIVOT_WEEKS = 13  # a swing is the extreme of +/- 13 weeks
LOOKBACK_WEEKS = 1040  # 20 years to find the trend origin
MAX_BEYOND = 0.10  # a rail with more weekly closes beyond it is not a rail

ZONES = ("below", "lower_air", "lower_body", "upper_body", "upper_air", "above")
BODY = (2, 3)
ROLES = ("lower", "air_lower", "center", "air_upper", "upper")
FRACTIONS = {"lower": 0.0, "air_lower": AIR, "center": 0.5, "air_upper": 1 - AIR, "upper": 1.0}
SOLID = 0  # MT4 STYLE_SOLID
DAY = 86400


@dataclass(frozen=True)
class TF:
    """Per-timeframe bar counts; the geometry (``AIR``, ``MAX_BEYOND``) is shared."""

    name: str
    period: int  # MT4 minutes, the .hst suffix
    granularity: str  # OANDA
    bar_secs: int
    stamp_shift: int  # OANDA open (broker time) -> MT4 bar stamp
    unit: str  # output keys: "weeks", "lines_next_week", ...
    bars_per_year: int
    lookback: int  # bars searched for the trend origin
    min_bars: int
    min_rail_span: int  # bars a hull edge must span to be the trend rail
    pivot: int  # a swing is the extreme of +/- this many bars
    visit_gap: int
    touch_gap: int
    touch_tol: float


TFS = {
    "W1": TF("W1", 10080, "W", WEEK, W1_STAMP_SHIFT, "week", 52, LOOKBACK_WEEKS, 3 * 52, 52,
             PIVOT_WEEKS, VISIT_GAP, TOUCH_GAP, TOUCH_TOL),
    # 1 year of daily bars to find the origin; OANDA's 17:00 NY day opens 00:00 broker
    "D1": TF("D1", 1440, "D", DAY, 0, "day", 260, 260, 120, 20, 5, 2, 3, TOUCH_TOL),
}
W1 = TFS["W1"]


def get_tf(timeframe: str | TF) -> TF:
    if isinstance(timeframe, TF):
        return timeframe
    key = str(timeframe).strip().upper()
    if key not in TFS:
        raise ChannelError(f"timeframe must be one of {', '.join(TFS)}, not {timeframe!r}")
    return TFS[key]


class ChannelError(ValueError):
    """Raised when bars or chart objects cannot produce a grid."""


# ---------------------------------------------------------------- bars


def pip_size(instrument: str) -> float:
    return 0.01 if "JPY" in mt4_bridge.map_symbol(instrument) else 0.0001


def hst_path(instrument: str, server: str = DEFAULT_SERVER, tf: str | TF = W1) -> Path:
    history = mt4_bridge.files_dir().parent.parent / "history" / server
    return history / f"{mt4_bridge.map_symbol(instrument)}{get_tf(tf).period}.hst"


def read_w1_hst(
    instrument: str, server: str = DEFAULT_SERVER, now_broker: int | None = None
) -> list[dict]:
    return read_hst_bars(instrument, W1, server, now_broker)


def read_hst_bars(
    instrument: str, tf: str | TF = W1, server: str = DEFAULT_SERVER, now_broker: int | None = None
) -> list[dict]:
    tf = get_tf(tf)
    path = hst_path(instrument, server, tf)
    _digits, recs = read_hst(path)
    if not recs:
        raise ChannelError(f"no {tf.name} history at {path}")
    now_broker = now_broker if now_broker is not None else broker_now()
    return [
        {
            "time": int(r[0]),
            "open": r[1],
            "high": r[2],
            "low": r[3],
            "close": r[4],
            "complete": int(r[0]) + tf.bar_secs <= now_broker,
        }
        for _t, r in sorted(recs.items())
    ]


def broker_now() -> int:
    return broker_ts_from_utc(datetime.now(timezone.utc))


def merge_oanda_weeks(bars: list[dict], candles: list[dict]) -> tuple[list[dict], int]:
    return merge_oanda_bars(bars, candles, W1)


def merge_oanda_bars(
    bars: list[dict], candles: list[dict], tf: str | TF = W1
) -> tuple[list[dict], int]:
    """Append/replace with OANDA bid candles stamped like MT4 bars.

    D1 skips weekend-stamped candles: the .hst export folds them away.
    """
    tf = get_tf(tf)
    by_time = {b["time"]: dict(b) for b in bars}
    last = bars[-1]["time"] if bars else 0
    added = 0
    for c in candles:
        bid = c.get("bid")
        if not bid:
            continue
        t = broker_ts(c["time"]) + tf.stamp_shift
        if t < last:
            continue
        if tf.bar_secs == DAY and datetime.fromtimestamp(t, timezone.utc).weekday() >= 5:
            continue
        if t not in by_time:
            added += 1
        by_time[t] = {
            "time": t,
            "open": float(bid["o"]),
            "high": float(bid["h"]),
            "low": float(bid["l"]),
            "close": float(bid["c"]),
            "complete": bool(c.get("complete", True)),
        }
    return [by_time[t] for t in sorted(by_time)], added


async def load_w1_bars(
    instrument: str, *, server: str = DEFAULT_SERVER, fetch: bool = True
) -> dict[str, Any]:
    """W1 bars MT4 draws, plus OANDA weeks newer than the .hst file."""
    return await load_bars(instrument, W1, server=server, fetch=fetch)


async def load_bars(
    instrument: str, tf: str | TF = W1, *, server: str = DEFAULT_SERVER, fetch: bool = True
) -> dict[str, Any]:
    """Bars MT4 draws on ``tf``, plus OANDA bars newer than the .hst file."""
    tf = get_tf(tf)
    bars = read_hst_bars(instrument, tf, server)
    added = 0
    note = ""
    if fetch:
        from app import oanda_client

        # every bar since the .hst file ends, plus a few to replace its last ones
        count = min(5000, max(6, (broker_now() - bars[-1]["time"]) // tf.bar_secs + 3))
        try:
            payload = await oanda_client.get_candles(
                instrument, granularity=tf.granularity, count=count, price="B"
            )
            bars, added = merge_oanda_bars(bars, payload.get("candles") or [], tf)
        except Exception as exc:  # noqa: BLE001 - research read; report and go on
            note = f"OANDA fetch failed, .hst only: {exc}"
    out = {"bars": bars, "timeframe": tf.name, "oanda_bars_added": added, "note": note}
    if tf is W1:
        out["oanda_weeks_added"] = added
    return out


def _date(t: int, tf: TF = W1) -> str:
    fmt = "%Y-%m-%d" if tf.bar_secs >= DAY else "%Y-%m-%d %H:%M"
    return datetime.fromtimestamp(int(t), timezone.utc).strftime(fmt)


# ---------------------------------------------------------------- lines


@dataclass(frozen=True)
class Line:
    """Two points by bar index (fractional beyond the last bar)."""

    i1: float
    p1: float
    i2: float
    p2: float

    @property
    def slope(self) -> float:
        return (self.p2 - self.p1) / (self.i2 - self.i1)

    def at(self, i: float) -> float:
        return self.p1 + self.slope * (i - self.i1)

    def shifted(self, dp: float) -> Line:
        return Line(self.i1, self.p1 + dp, self.i2, self.p2 + dp)


class BarIndex:
    """Bar time ↔ index, extrapolating one bar per ``bar_secs`` past either end.

    D1 extrapolation counts weekdays only, as MT4 has no weekend bars.
    """

    def __init__(self, bars: list[dict], bar_secs: int = WEEK) -> None:
        self.times = [int(b["time"]) for b in bars]
        self.bar_secs = bar_secs

    def _bars_between(self, t0: int, t1: int) -> float:
        if self.bar_secs != DAY:
            return (t1 - t0) / self.bar_secs
        sign = 1 if t1 >= t0 else -1
        a, b = sorted((t0, t1))
        days = 0
        d = a + DAY
        while d <= b:
            if datetime.fromtimestamp(d, timezone.utc).weekday() < 5:
                days += 1
            d += DAY
        return sign * days

    def index(self, t: int) -> float:
        times = self.times
        if t >= times[-1]:
            return len(times) - 1 + self._bars_between(times[-1], t)
        if t <= times[0]:
            return self._bars_between(times[0], t)
        k = bisect.bisect_right(times, t) - 1
        return float(k)

    def _step(self, t: int, k: int) -> int:
        if self.bar_secs != DAY:
            return t + k * self.bar_secs
        step = DAY if k > 0 else -DAY
        for _ in range(abs(k)):
            t += step
            while datetime.fromtimestamp(t, timezone.utc).weekday() >= 5:
                t += step
        return t

    def time(self, i: float) -> int:
        n = len(self.times)
        k = int(round(i))
        if 0 <= k < n:
            return self.times[k]
        if k >= n:
            return self._step(self.times[-1], k - n + 1)
        return self._step(self.times[0], k)


@dataclass
class Grid:
    """Five lines; ``base`` is the channel object's anchored line (t1/t2)."""

    base: Line
    lines: dict[str, Line]
    anchors: dict[str, Any]
    names: dict[str, str]
    source: str
    warnings: list[str]
    tf: TF = W1

    @property
    def direction(self) -> str:
        return "up" if self.lines["lower"].slope > 0 else "down"

    def values(self, i: float) -> list[float]:
        return [self.lines[r].at(i) for r in ROLES]


def _grid(base: Line, width: float, anchors: dict[str, Any], source: str, tf: TF = W1) -> Grid:
    """``base`` + ``width`` (signed: + puts the other rail above)."""
    lower = base if width > 0 else base.shifted(width)
    w = abs(width)
    lines = {r: lower.shifted(f * w) for r, f in FRACTIONS.items()}
    return Grid(base=base, lines=lines, anchors=anchors, names={}, source=source, warnings=[], tf=tf)


# ---------------------------------------------------------------- proposal


def _hull(points: list[tuple[int, float]], lower: bool) -> list[tuple[int, float]]:
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    out: list[tuple[int, float]] = []
    for p in points:
        while len(out) >= 2 and (cross(out[-2], out[-1], p) <= 0) == lower:
            out.pop()
        out.append(p)
    return out


def _pivots(values: list[float], start: int, high: bool, span: int = PIVOT_WEEKS) -> list[int]:
    out = []
    n = len(values)
    for i in range(start, n):
        win = values[max(start, i - span) : min(n, i + span + 1)]
        if values[i] == (max(win) if high else min(win)):
            if not out or i - out[-1] > span:
                out.append(i)
    return out


def _episodes(idx: list[int], gap: int) -> list[list[int]]:
    groups: list[list[int]] = []
    for i in idx:
        if groups and i - groups[-1][-1] <= gap + 1:
            groups[-1].append(i)
        else:
            groups.append([i])
    return groups


def propose_channel(
    bars: list[dict],
    tf: str | TF = W1,
    *,
    lookback: int | None = None,
    tol: float | None = None,
    max_candidates: int = 4,
) -> dict[str, Any]:
    """Trend origin, anchored rail, and ranked widths for the opposite rail.

    Origin: the extreme of the last ``tf.lookback`` bars that came first (low →
    up trend, high → down): 20 years on W1, 1 year on D1.
    Anchored rail: the hull edge of lows (up) or highs (down) since the origin
    with the most touch episodes, ties to the longer edge. Opposite rail:
    each swing's distance from the anchored rail is a candidate width, scored
    by how many other swings sit on it (within ``tol``); fewer than two, or
    more than 10% of closes beyond, and it is dropped. Ranked by
    touches, then by fewer closes beyond; one per cluster of nearby widths. The widest swing ("extreme", nothing
    beyond it) is always listed. Choosing among them is the trader's call.
    """
    tf = get_tf(tf)
    lookback = tf.lookback if lookback is None else lookback
    tol = tf.touch_tol if tol is None else tol
    bars = [b for b in bars if b.get("complete", True)]
    n = len(bars)
    if n < tf.min_bars:
        raise ChannelError(f"need at least {tf.min_bars} {tf.name} bars")
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    closes = [b["close"] for b in bars]
    w0 = max(0, n - lookback)
    i_lo = min(range(w0, n), key=lambda i: lows[i])
    i_hi = max(range(w0, n), key=lambda i: highs[i])
    up = i_lo < i_hi
    origin = i_lo if up else i_hi
    anchor_vals = lows if up else highs
    other_vals = highs if up else lows
    sign = 1 if up else -1

    hull = _hull([(i, anchor_vals[i]) for i in range(origin, n)], lower=up)
    best = None
    for a, b in zip(hull, hull[1:]):
        line = Line(a[0], a[1], b[0], b[1])
        if line.slope * sign <= 0 or b[0] - a[0] < tf.min_rail_span:
            continue
        near = [
            i for i in range(origin, n) if abs(anchor_vals[i] - line.at(i)) <= tol * line.at(i)
        ]
        touches = len(_episodes(near, tf.touch_gap))
        key = (touches, b[0] - a[0])
        if best is None or key > best[0]:
            best = (key, line, a[0], b[0])
    if best is None:
        raise ChannelError(
            f"no trend rail: the hull since the origin has no edge {tf.min_rail_span} {tf.unit}s long"
        )
    (_t, _span), base, ia, ib = best

    # swings by distance from the anchored rail, not raw price: in a trend
    # channel the raw extreme comes late in each swing
    dist = [0.0] * origin + [(other_vals[i] - base.at(i)) * sign for i in range(origin, n)]
    offs = {i: dist[i] for i in _pivots(dist, origin, high=True, span=tf.pivot) if dist[i] > 0}
    if not offs:
        raise ChannelError("no swing on the far side of the trend rail")

    def score(width: float) -> dict[str, Any]:
        on = [i for i, o in offs.items() if abs(o - width) <= tol * other_vals[i]]
        beyond = sum(
            1 for i in range(origin, n) if (closes[i] - base.at(i)) * sign > width
        )
        return {"touches": sorted(on), "closes_beyond": beyond}

    cands = []
    seen: set[float] = set()
    extreme = max(offs, key=offs.get)
    for i, o in sorted(offs.items(), key=lambda kv: -kv[1]):
        if round(o, 6) in seen:
            continue
        seen.add(round(o, 6))
        s = score(o)
        if i != extreme and (len(s["touches"]) < 2 or s["closes_beyond"] > MAX_BEYOND * (n - origin)):
            continue
        cands.append((i, o, s))
    span = n - origin
    cands.sort(key=lambda c: (-len(c[2]["touches"]), c[2]["closes_beyond"]))
    # one candidate per cluster of swings that sit on the same line
    distinct: list[tuple[int, float, dict]] = []
    for c in cands:
        if all(abs(c[1] - d[1]) > tol * other_vals[c[0]] for d in distinct) or c[0] == extreme:
            distinct.append(c)
    cands = distinct
    # keep the extreme visible even when it ranks low
    ranked = cands[:max_candidates]
    if all(c[0] != extreme for c in ranked):
        ranked = ranked[: max_candidates - 1] + [c for c in cands if c[0] == extreme]

    out_cands = []
    for k, (i3, o, s) in enumerate(ranked):
        out_cands.append(
            {
                "candidate": k,
                "width": round(o, 6),
                "anchor": {"time": bars[i3]["time"], "date": _date(bars[i3]["time"], tf),
                           "price": other_vals[i3]},
                "touches": [_date(bars[i]["time"], tf) for i in s["touches"]],
                "closes_beyond_pct": round(100 * s["closes_beyond"] / span, 1),
                "extreme": i3 == extreme,
            }
        )
    out: dict[str, Any] = {} if tf is W1 else {"timeframe": tf.name}
    return {
        **out,
        "direction": "up" if up else "down",
        "origin": {"date": _date(bars[origin]["time"], tf), "time": bars[origin]["time"],
                   "price": anchor_vals[origin]},
        "rail": {
            "side": "lower" if up else "upper",
            "t1": bars[ia]["time"], "p1": anchor_vals[ia], "date1": _date(bars[ia]["time"], tf),
            "t2": bars[ib]["time"], "p2": anchor_vals[ib], "date2": _date(bars[ib]["time"], tf),
            "touches": best[0][0],
        },
        f"slope_per_{tf.unit}": base.slope,
        "candidates": out_cands,
        "last_bar": _date(bars[-1]["time"], tf),
    }


def grid_from_proposal(
    bars: list[dict], proposal: dict[str, Any], candidate: int = 0, tf: str | TF = W1
) -> Grid:
    tf = get_tf(tf)
    cands = proposal["candidates"]
    if not 0 <= candidate < len(cands):
        raise ChannelError(f"candidate {candidate} not in 0..{len(cands) - 1}")
    c = cands[candidate]
    rail = proposal["rail"]
    index = BarIndex(bars, tf.bar_secs)
    base = Line(index.index(rail["t1"]), rail["p1"], index.index(rail["t2"]), rail["p2"])
    sign = 1 if proposal["direction"] == "up" else -1
    anchors = {
        "t1": rail["t1"], "p1": rail["p1"], "t2": rail["t2"], "p2": rail["p2"],
        "t3": c["anchor"]["time"], "p3": c["anchor"]["price"],
        "start": proposal["origin"]["time"],
    }
    return _grid(base, sign * c["width"], anchors, f"proposal candidate {candidate}", tf)


# ---------------------------------------------------------------- chart


def _obj_line(obj: dict, index: BarIndex) -> Line:
    t1, t2 = int(obj["t1"]), int(obj["t2"])
    if t1 == t2:
        raise ChannelError(f"{obj.get('name')}: both points on the same bar")
    return Line(index.index(t1), float(obj["p1"]), index.index(t2), float(obj["p2"]))


def _tooltip_role(obj: dict) -> str:
    for tok in str(obj.get("tooltip") or "").split():
        if tok.startswith("role="):
            return tok[5:].strip().lower().replace("-", "_")
    return ""


def grid_from_chart(
    bars: list[dict], objects: list[dict], channel_name: str = "", tf: str | TF = W1
) -> Grid:
    """The trader's grid from ``chart.json`` objects (roles from style/position).

    Rails come from the channel object (with several: the one tagged
    ``role=channel``, else the one whose name ends in the timeframe, as drawn by
    ``grid_objects``; else pass ``channel_name``). A trendline parallel to it (slope
    within 2% of the rails' rise over the chart) and between the rails is the
    center line if solid and the nearest air boundary if dashed/dotted;
    ``role=`` in the tooltip overrides. Missing lines fall back to the
    theoretical position and are named in ``warnings``.
    """
    tf = get_tf(tf)
    index = BarIndex(bars, tf.bar_secs)
    chans = [o for o in objects if o.get("type") == "channel"]
    if channel_name:
        chans = [o for o in chans if o.get("name") == channel_name]
    if not chans:
        raise ChannelError("no channel object on the chart" + (f" named {channel_name!r}" if channel_name else ""))
    if len(chans) > 1:
        tagged = [o for o in chans if _tooltip_role(o) == "channel"]
        if len(tagged) != 1:
            # a W1 grid stays on the chart when it is switched to D1; take this timeframe's
            tagged = [o for o in chans if str(o.get("name") or "").endswith(f" {tf.name}")]
        if len(tagged) != 1:
            raise ChannelError(
                "several channel objects; pass channel_name: "
                + ", ".join(str(o.get("name")) for o in chans)
            )
        chans = tagged
    ch = chans[0]
    base = _obj_line(ch, index)
    i3 = index.index(int(ch["t3"]))
    width = float(ch["p3"]) - base.at(i3)
    if abs(width) < 1e-12:
        raise ChannelError(f"{ch.get('name')}: zero-width channel")
    grid = _grid(base, width, {k: ch[k] for k in ("t1", "p1", "t2", "p2", "t3", "p3")}, "chart", tf)
    grid.names = {"lower": str(ch.get("name")), "upper": str(ch.get("name"))}
    w = abs(width)
    last = len(bars) - 1
    rise = abs(base.slope) * max(1, last - base.i1) + w

    found: dict[str, tuple[float, dict]] = {}
    for obj in objects:
        if obj.get("type") != "trend":
            continue
        line = _obj_line(obj, index)
        if abs(line.slope - base.slope) * max(1, last - base.i1) > 0.02 * rise:
            continue
        frac = (line.at(last) - grid.lines["lower"].at(last)) / w
        if not 0.02 < frac < 0.98:
            continue
        role = _tooltip_role(obj)
        if role not in ("air_lower", "center", "air_upper"):
            if obj.get("style") in (None, SOLID, "solid"):
                role = "center"
            else:
                role = "air_lower" if frac < 0.5 else "air_upper"
        dist = abs(frac - FRACTIONS[role])
        if role not in found or dist < found[role][0]:
            found[role] = (dist, obj)
    for role in ("air_lower", "center", "air_upper"):
        if role in found:
            obj = found[role][1]
            grid.lines[role] = _obj_line(obj, index)
            grid.names[role] = str(obj.get("name"))
        else:
            grid.warnings.append(f"no {role} line on the chart; using {FRACTIONS[role]:.0%} of the width")
    mid = (grid.lines["lower"].at(last) + grid.lines["upper"].at(last)) / 2
    grid.anchors["true_midline"] = mid
    return grid


# ---------------------------------------------------------------- read


def _zone(close: float, vals: list[float]) -> int:
    return sum(close > v for v in vals)


def _runs(zones: list[tuple[int, int]]) -> list[dict]:
    runs: list[dict] = []
    for i, z in zones:
        if runs and runs[-1]["z"] == z:
            runs[-1]["end"] = i
            runs[-1]["n"] += 1
        else:
            runs.append({"z": z, "start": i, "end": i, "n": 1})
    return runs


def _visits(runs: list[dict], target: int, gap: int = VISIT_GAP) -> list[dict]:
    out: list[dict] = []
    for k, r in enumerate(runs):
        if r["z"] != target:
            continue
        if out and r["start"] - out[-1]["end"] <= gap + 1:
            out[-1].update(end=r["end"], nxt=k + 1)
            out[-1]["closes"] += r["n"]
            continue
        out.append({"start": r["start"], "end": r["end"], "closes": r["n"], "prev": k - 1, "nxt": k + 1})
    return out


def read_grid(
    bars: list[dict], grid: Grid, *, instrument: str = "", start_time: int | None = None
) -> dict[str, Any]:
    """The §3 read, by bar close (the in-progress bar excluded).

    W1 keys say ``week`` (``weeks``, ``lines_next_week``); other timeframes
    use their unit (``days``, ``lines_next_day``) and add ``timeframe``.
    """
    tf = grid.tf
    unit = tf.unit
    done = [b for b in bars if b.get("complete", True)]
    if not done:
        raise ChannelError(f"no completed {tf.name} bar")
    n = len(done)
    index = BarIndex(done, tf.bar_secs)
    a = grid.anchors
    first = a.get("start") or min(int(a["t1"]), int(a["t2"]), int(a["t3"]))
    start = int(max(0, index.index(start_time if start_time is not None else int(first))))
    if start >= n:
        raise ChannelError("grid starts after the last completed bar")
    pip = pip_size(instrument) if instrument else (0.01 if done[-1]["close"] > 20 else 0.0001)
    lower = grid.lines["lower"]

    zones = [(i, _zone(done[i]["close"], grid.values(i))) for i in range(start, n)]
    runs = _runs(zones)
    count = len(zones)
    shares = {ZONES[z]: round(100 * sum(1 for _i, zz in zones if zz == z) / count, 1) for z in range(6)}

    visits: dict[str, list[dict]] = {}
    for target in (0, 1, 4, 5):
        rows = []
        for v in _visits(runs, target, tf.visit_gap):
            span = done[v["start"] : v["end"] + 1]
            ext = max(b["high"] for b in span) if target >= 4 else min(b["low"] for b in span)
            frm = ZONES[runs[v["prev"]]["z"]] if v["prev"] >= 0 else None
            to = ZONES[runs[v["nxt"]]["z"]] if v["nxt"] < len(runs) else "now"
            rows.append(
                {
                    "from_date": _date(done[v["start"]]["time"], tf),
                    "to_date": _date(done[v["end"]]["time"], tf),
                    "closes": v["closes"],
                    f"{unit}s": v["end"] - v["start"] + 1,
                    "extreme": ext,
                    "came_from": frm,
                    "left_to": to,
                }
            )
        visits[ZONES[target]] = rows

    cur_z = zones[-1][1]
    cur = None
    if cur_z in (0, 1, 4, 5):
        cur = visits[ZONES[cur_z]][-1]
    else:
        r = runs[-1]
        cur = {"from_date": _date(done[r["start"]]["time"], tf), "closes": r["n"],
               f"{unit}s": r["end"] - r["start"] + 1}
    outcomes: dict[str, int] = {}
    if cur_z in (0, 1, 4, 5):
        for v in visits[ZONES[cur_z]][:-1]:
            key = "body" if v["left_to"] in ("lower_body", "upper_body") else v["left_to"]
            outcomes[key] = outcomes.get(key, 0) + 1

    last = n - 1
    vals = grid.values(last)
    nxt = grid.values(last + 1)
    close = done[last]["close"]
    rail = "upper" if abs(close - vals[4]) < abs(close - vals[0]) else "lower"
    rline = grid.lines[rail]
    near = [
        i for i in range(start, n)
        if done[i]["low"] <= rline.at(i) * (1 + tf.touch_tol) and done[i]["high"] >= rline.at(i) * (1 - tf.touch_tol)
        or (done[i]["low"] > rline.at(i) if rail == "upper" else done[i]["high"] < rline.at(i))
    ]
    tests = []
    for ep in _episodes(near, tf.touch_gap)[-8:]:
        rng = range(ep[0], ep[-1] + 1)
        beyond = sum(
            1 for i in rng
            if (done[i]["close"] > rline.at(i) if rail == "upper" else done[i]["close"] < rline.at(i))
        )
        tests.append(
            {
                "from_date": _date(done[ep[0]]["time"], tf),
                "to_date": _date(done[ep[-1]]["time"], tf),
                f"{unit}s": len(rng),
                "max_poke_above": round(max(done[i]["high"] - rline.at(i) for i in rng), 6),
                "max_poke_below": round(max(rline.at(i) - done[i]["low"] for i in rng), 6),
                "closes_beyond": beyond,
            }
        )

    slope = lower.slope
    levels: dict[str, Any] = {}
    if cur_z in (4, 5):
        levels = {"body_return_close_below": nxt[3], "then_center": nxt[2], "breakout_close_above": nxt[4]}
    elif cur_z in (0, 1):
        levels = {"body_return_close_above": nxt[1], "then_center": nxt[2], "breakdown_close_below": nxt[0]}
    else:
        levels = {"upper_air_close_above": nxt[3], "center": nxt[2], "lower_air_close_below": nxt[1]}

    head: dict[str, Any] = {} if tf is W1 else {"timeframe": tf.name}
    return {
        **head,
        "source": grid.source,
        "names": grid.names,
        "warnings": grid.warnings,
        "trend": {
            "direction": grid.direction,
            f"pips_per_{unit}": round(slope / pip, 2),
            "pips_per_year": round(slope * tf.bars_per_year / pip, 1),
            "since": _date(done[start]["time"], tf),
        },
        "last_close": {"date": _date(done[last]["time"], tf), "close": close},
        "lines_now": dict(zip(ROLES, (round(v, 6) for v in vals))),
        f"lines_next_{unit}": dict(zip(ROLES, (round(v, 6) for v in nxt))),
        f"true_midline_next_{unit}": round((nxt[0] + nxt[4]) / 2, 6),
        "zone": {"now": ZONES[cur_z], **(cur or {})},
        "zone_shares_pct": shares,
        f"{unit}s": count,
        "visits": visits,
        "past_outcomes_of_current_zone": outcomes,
        "rail_tests": {"rail": rail, "episodes": tests},
        "levels": {k: round(v, 6) for k, v in levels.items()},
        "note": f"Counts, not probabilities: a {tf.name} channel gives a handful of air visits.",
    }


# ---------------------------------------------------------------- draw


def grid_objects(grid: Grid, instrument: str, color: str = "#FFFF00") -> list[dict[str, Any]]:
    """Editable bridge objects: one channel plus three trendlines."""
    sym = mt4_bridge.map_symbol(instrument)
    tf = grid.tf.name
    a = grid.anchors
    objs: list[dict[str, Any]] = [
        {
            "name": f"Channel {sym} {tf}",
            "type": "channel",
            "t1": int(a["t1"]), "p1": float(a["p1"]),
            "t2": int(a["t2"]), "p2": float(a["p2"]),
            "t3": int(a["t3"]), "p3": float(a["p3"]),
            "color": color, "style": "solid", "ray": True, "editable": True,
        }
    ]
    for role, label, style in (
        ("air_upper", "air-upper", "dot"),
        ("center", "center", "solid"),
        ("air_lower", "air-lower", "dot"),
    ):
        shift = grid.lines[role].at(grid.base.i1) - grid.base.p1
        objs.append(
            {
                "name": f"Trendline {sym} {tf} {label}",
                "type": "trend",
                "t1": int(a["t1"]), "p1": round(float(a["p1"]) + shift, 6),
                "t2": int(a["t2"]), "p2": round(float(a["p2"]) + shift, 6),
                "color": color, "style": style, "ray": True, "editable": True,
            }
        )
    return objs


def draw_grid(instrument: str, grid: Grid) -> dict[str, Any]:
    """Draw the grid on its live chart (``grid.tf``); existing editable lines are kept."""
    from app import trader_outbox

    tf = grid.tf.name
    ok, reason = mt4_bridge.check_chart(instrument, tf)
    if not ok:
        return {"ok": False, "error": reason}
    objs = grid_objects(grid, instrument)
    chart = trader_outbox.load_chart(mt4_bridge.chart_dir(instrument, tf))
    present = {o.get("name") for o in chart.get("objects") or []}
    result = mt4_bridge.upsert_objects(
        instrument, tf, objs, prefix="sbox.mtf.", clear_prefix_first=True
    )
    result.pop("status", None)
    result["drawn"] = [o["name"] for o in objs if o["name"] not in present]
    result["already_present"] = [o["name"] for o in objs if o["name"] in present]
    return result


def wait_for_capture(
    instrument: str, names: list[str], timeout: float = 5.0, timeframe: str = "W1"
) -> bool:
    """True once every name shows up in the EA's chart.json."""
    from app import trader_outbox

    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        chart = trader_outbox.load_chart(mt4_bridge.chart_dir(instrument, timeframe))
        if set(names) <= {o.get("name") for o in chart.get("objects") or []}:
            return True
        _time.sleep(0.2)
    return False


# ---------------------------------------------------------------- W1 context


async def w1_context(
    instrument: str, *, fetch: bool = True, channel_name: str = ""
) -> dict[str, Any]:
    """The W1 read a lower-timeframe grid is reported against.

    Uses the trader's W1 grid from the last W1 ``chart.json`` capture (live or
    not), else proposal candidate 0, and says which in ``source``. Errors come
    back as ``{"error": ...}`` so the lower-timeframe read still stands.
    """
    from app import trader_outbox

    try:
        bars = (await load_bars(instrument, W1, fetch=fetch))["bars"]
        live, reason = mt4_bridge.check_chart(instrument, "W1")
        objects = trader_outbox.load_chart(mt4_bridge.chart_dir(instrument, "W1")).get("objects") or []
        try:
            grid = grid_from_chart(bars, objects, channel_name)
            if not live:
                grid.warnings.append(f"W1 chart not live, using its last capture: {reason}")
        except ChannelError as exc:
            grid = grid_from_proposal(bars, propose_channel(bars))
            grid.source = f"proposal candidate 0 (no W1 chart grid: {exc})"
        r = read_grid(bars, grid, instrument=instrument)
    except (ChannelError, mt4_bridge.Mt4BridgeError, OSError, ValueError) as exc:
        return {"error": str(exc)}
    return {k: r[k] for k in ("source", "warnings", "trend", "last_close", "zone",
                              "lines_next_week", "levels")}
