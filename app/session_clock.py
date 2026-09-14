"""Calendar join on OHLCV timestamps: FX session labels and Lien Ch.11 windows.

OANDA candle ``time`` is RFC3339 UTC (bar open). Session hours use IANA zones
(``Europe/London``, ``Europe/Berlin``, ``America/New_York``, ``Asia/Tokyo``) so
DST is not a hardcoded table. Research only; no orders. News/FOMC is not a
calendar field — do not invent event days from range fatness.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.mt4_bridge import parse_rfc3339_utc

ClockMode = Literal["frankfurt", "utc_fixed"]

TZ_UTC = ZoneInfo("UTC")
TZ_LONDON = ZoneInfo("Europe/London")
TZ_BERLIN = ZoneInfo("Europe/Berlin")
TZ_NY = ZoneInfo("America/New_York")
TZ_TOKYO = ZoneInfo("Asia/Tokyo")

# Book literal (chunk 81): 06:00–07:00 GMT. Summer Frankfurt→London matches this;
# winter Frankfurt→London is 07:00–08:00 UTC. Default clock is DST-aware.
UTC_FIXED_POWER_HOUR = (6, 7)
FRANKFURT_OPEN = time(8, 0)
LONDON_OPEN = time(8, 0)
TOKYO_OPEN, TOKYO_CLOSE = 9, 18
LONDON_SESSION_OPEN, LONDON_SESSION_CLOSE = 8, 16
NY_SESSION_OPEN, NY_SESSION_CLOSE = 8, 17

NEWS_NOTE = "news filter unavailable"


def parse_bar_datetime(value: str | datetime | None) -> datetime | None:
    """Bar open as timezone-aware UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    unix = parse_rfc3339_utc(str(value))
    if unix is None:
        return None
    return datetime.fromtimestamp(unix, tz=timezone.utc)


def tag_bar(value: str | datetime | None) -> dict[str, Any] | None:
    """Join a bar timestamp to weekday / hours / session / London date."""
    dt = parse_bar_datetime(value)
    if dt is None:
        return None
    london = dt.astimezone(TZ_LONDON)
    berlin = dt.astimezone(TZ_BERLIN)
    ny = dt.astimezone(TZ_NY)
    tokyo = dt.astimezone(TZ_TOKYO)
    london_hour = london.hour
    berlin_hour = berlin.hour
    ny_hour = ny.hour
    tokyo_hour = tokyo.hour
    in_tokyo = TOKYO_OPEN <= tokyo_hour < TOKYO_CLOSE
    in_london = LONDON_SESSION_OPEN <= london_hour < LONDON_SESSION_CLOSE
    in_ny = NY_SESSION_OPEN <= ny_hour < NY_SESSION_CLOSE
    if in_london and in_ny:
        session = "overlap_london_ny"
    elif in_tokyo and in_london:
        session = "overlap_tokyo_london"
    elif in_london:
        session = "london"
    elif in_ny:
        session = "new_york"
    elif in_tokyo:
        session = "tokyo"
    else:
        session = "other"
    return {
        "time_utc": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "weekday": london.weekday(),
        "hour_utc": dt.hour,
        "london_hour": london_hour,
        "berlin_hour": berlin_hour,
        "ny_hour": ny_hour,
        "tokyo_hour": tokyo_hour,
        "session": session,
        "london_date": london.date().isoformat(),
        "in_london_session": in_london,
        "weekend": london.weekday() >= 5,
    }


def power_hour_bounds(
    london_date: date,
    clock: ClockMode = "frankfurt",
) -> tuple[datetime, datetime]:
    """Half-open UTC window for the Frankfurt→London power hour on ``london_date``."""
    if clock == "utc_fixed":
        start = datetime(
            london_date.year,
            london_date.month,
            london_date.day,
            UTC_FIXED_POWER_HOUR[0],
            0,
            tzinfo=TZ_UTC,
        )
        end = datetime(
            london_date.year,
            london_date.month,
            london_date.day,
            UTC_FIXED_POWER_HOUR[1],
            0,
            tzinfo=TZ_UTC,
        )
        return start, end
    start_local = datetime.combine(london_date, FRANKFURT_OPEN, tzinfo=TZ_BERLIN)
    end_local = datetime.combine(london_date, LONDON_OPEN, tzinfo=TZ_LONDON)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _in_window(dt: datetime, start: datetime, end: datetime) -> bool:
    return start <= dt < end


def bars_on_london_date(
    bars: list[dict[str, Any]],
    london_date: date,
    *,
    end_index: int | None = None,
) -> list[dict[str, Any]]:
    """Causal slice: bars whose London date matches, up to ``end_index`` inclusive."""
    if end_index is None:
        end_index = len(bars) - 1
    if end_index < 0 or end_index >= len(bars):
        return []
    wanted = london_date.isoformat()
    out: list[dict[str, Any]] = []
    for j in range(end_index, -1, -1):
        tagged = tag_bar(bars[j].get("time"))
        if tagged is None:
            continue
        if tagged["london_date"] != wanted:
            if out:
                break
            continue
        out.append(bars[j])
    out.reverse()
    return out


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _hlc(bar: dict[str, Any]) -> tuple[float, float, float] | None:
    high, low, close = _f(bar.get("high")), _f(bar.get("low")), _f(bar.get("close"))
    if high is None or low is None or close is None:
        return None
    return high, low, close


def waiting_deal_state(
    bars: list[dict[str, Any]],
    *,
    pip: float,
    hunt_pips: float = 25.0,
    entry_buffer_pips: float = 10.0,
    stop_pips: float = 25.0,
    clock: ClockMode = "frankfurt",
    end_index: int | None = None,
) -> dict[str, Any]:
    """Power-hour range, first ≥hunt probe, then reverse through the opposite rail.

    Causal on ``bars[:end_index+1]``. Same-bar hunt+reverse is allowed (OHLC
    has no wick order). Weekend London dates do not set up.
    """
    if end_index is None:
        end_index = len(bars) - 1
    empty = {
        "clock": clock,
        "power_hour_complete": False,
        "range_high": None,
        "range_low": None,
        "range_pips": None,
        "hunt_side": None,
        "hunt_extreme": None,
        "hunt_pips": None,
        "reversed": False,
        "pending_side": None,
        "pending_entry": None,
        "pending_stop": None,
        "session": None,
        "london_date": None,
        "note": NEWS_NOTE,
        "reason": "no bars",
    }
    if end_index < 0 or end_index >= len(bars) or pip <= 0:
        return empty

    last_tag = tag_bar(bars[end_index].get("time"))
    if last_tag is None:
        empty["reason"] = "unparseable bar time"
        return empty
    london_date = date.fromisoformat(last_tag["london_date"])
    last_dt = parse_bar_datetime(bars[end_index].get("time"))
    assert last_dt is not None
    start, end = power_hour_bounds(london_date, clock=clock)
    session_bars = bars_on_london_date(bars, london_date, end_index=end_index)
    base = {
        **empty,
        "session": last_tag,
        "london_date": last_tag["london_date"],
        "power_hour_start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "power_hour_end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reason": "watching",
    }
    if last_tag["weekend"]:
        base["reason"] = "weekend: no London deal setup"
        return base

    power: list[dict[str, Any]] = []
    post: list[dict[str, Any]] = []
    for bar in session_bars:
        dt = parse_bar_datetime(bar.get("time"))
        if dt is None:
            continue
        if _in_window(dt, start, end):
            power.append(bar)
        elif dt >= end:
            post.append(bar)

    complete = last_dt >= end
    base["power_hour_complete"] = complete
    if not power:
        base["reason"] = (
            "power hour complete but no bars in window"
            if complete
            else "waiting for Frankfurt–London power hour"
        )
        return base

    highs, lows = [], []
    for bar in power:
        hlc = _hlc(bar)
        if hlc is None:
            continue
        highs.append(hlc[0])
        lows.append(hlc[1])
    if not highs:
        base["reason"] = "power-hour OHLC unavailable"
        return base
    range_high = max(highs)
    range_low = min(lows)
    base["range_high"] = range_high
    base["range_low"] = range_low
    base["range_pips"] = round((range_high - range_low) / pip, 1)
    if not complete:
        base["reason"] = "power hour still printing"
        return base

    hunt_excess = hunt_pips * pip
    entry_buffer = entry_buffer_pips * pip
    stop_dist = stop_pips * pip
    hunt_side: str | None = None
    hunt_extreme: float | None = None
    reversed_through = False
    pending_side: str | None = None
    pending_entry: float | None = None
    pending_stop: float | None = None

    for bar in post:
        hlc = _hlc(bar)
        if hlc is None:
            continue
        high, low, _close = hlc
        if hunt_side is None:
            up_excess = high - range_high
            down_excess = range_low - low
            hunted_up = up_excess >= hunt_excess
            hunted_down = down_excess >= hunt_excess
            if hunted_up and hunted_down:
                if up_excess >= down_excess:
                    hunt_side, hunt_extreme = "up", high
                else:
                    hunt_side, hunt_extreme = "down", low
            elif hunted_up:
                hunt_side, hunt_extreme = "up", high
            elif hunted_down:
                hunt_side, hunt_extreme = "down", low
            if hunt_side == "up":
                pending_side = "short"
                pending_entry = range_low - entry_buffer
                pending_stop = range_low + stop_dist
            elif hunt_side == "down":
                pending_side = "long"
                pending_entry = range_high + entry_buffer
                pending_stop = range_high - stop_dist
        if hunt_side is None or pending_entry is None:
            continue
        if hunt_side == "up" and low <= pending_entry:
            reversed_through = True
            break
        if hunt_side == "down" and high >= pending_entry:
            reversed_through = True
            break

    hunt_pips_now = None
    if hunt_side == "up" and hunt_extreme is not None:
        hunt_pips_now = round((hunt_extreme - range_high) / pip, 1)
    elif hunt_side == "down" and hunt_extreme is not None:
        hunt_pips_now = round((range_low - hunt_extreme) / pip, 1)

    base.update(
        {
            "hunt_side": hunt_side,
            "hunt_extreme": hunt_extreme,
            "hunt_pips": hunt_pips_now,
            "reversed": reversed_through,
            "pending_side": pending_side,
            "pending_entry": pending_entry,
            "pending_stop": pending_stop,
        }
    )
    if hunt_side is None:
        base["reason"] = "power hour done; waiting for ≥25-pip London hunt"
        return base
    if not reversed_through:
        verb = "high" if hunt_side == "up" else "low"
        base["reason"] = f"hunt {hunt_side} beyond power-hour {verb}; waiting for reverse"
        return base
    base["reason"] = (
        f"hunt {hunt_side} then reverse through opposite rail "
        f"({pending_side} pending)"
    )
    return base


def hour_of_week_ranges(
    bars: list[dict[str, Any]],
    pip: float,
) -> list[dict[str, Any]]:
    """Mean bar range by (London weekday, UTC hour). Not a PnL fit."""
    buckets: dict[tuple[int, int], list[float]] = defaultdict(list)
    for bar in bars:
        tagged = tag_bar(bar.get("time"))
        hlc = _hlc(bar)
        if tagged is None or hlc is None or pip <= 0:
            continue
        high, low, _ = hlc
        buckets[(tagged["weekday"], tagged["hour_utc"])].append((high - low) / pip)
    out: list[dict[str, Any]] = []
    for (weekday, hour_utc), ranges in sorted(buckets.items()):
        out.append(
            {
                "weekday": weekday,
                "hour_utc": hour_utc,
                "hour_of_week": weekday * 24 + hour_utc,
                "n": len(ranges),
                "mean_range_pips": round(sum(ranges) / len(ranges), 2),
            }
        )
    return out


def waiting_deal_day_outcomes(
    bars: list[dict[str, Any]],
    pip: float,
    *,
    hunt_pips: float = 25.0,
    clock: ClockMode = "frankfurt",
) -> dict[str, Any]:
    """One outcome per London weekday: hunt printed? reverse printed?

    Calendar correlation for the Ch.11 microstructure. No regime gate, no PnL.
    """
    by_date: dict[str, int] = {}
    for i, bar in enumerate(bars):
        tagged = tag_bar(bar.get("time"))
        if tagged is None or tagged["weekend"]:
            continue
        by_date[tagged["london_date"]] = i

    days = 0
    hunts = 0
    reverses = 0
    hunt_up = 0
    hunt_down = 0
    for last_i in by_date.values():
        state = waiting_deal_state(
            bars, pip=pip, hunt_pips=hunt_pips, clock=clock, end_index=last_i
        )
        if not state.get("power_hour_complete"):
            continue
        if state.get("range_high") is None:
            continue
        days += 1
        if state.get("hunt_side"):
            hunts += 1
            if state["hunt_side"] == "up":
                hunt_up += 1
            else:
                hunt_down += 1
        if state.get("reversed"):
            reverses += 1

    def _rate(num: int, den: int) -> float | None:
        if den <= 0:
            return None
        return round(num / den, 3)

    return {
        "clock": clock,
        "hunt_pips": hunt_pips,
        "days_with_power_hour": days,
        "hunts": hunts,
        "reverses": reverses,
        "hunt_up": hunt_up,
        "hunt_down": hunt_down,
        "hunt_rate": _rate(hunts, days),
        "reverse_rate_given_hunt": _rate(reverses, hunts),
        "reverse_rate": _rate(reverses, days),
        "note": NEWS_NOTE,
    }
