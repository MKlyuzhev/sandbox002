"""Ch. 7 geometry: named snapshot levels → entry / stop / 2R target.

Book chunks stay heuristic. Prices come from ``indicators.snapshot`` already
computed on OANDA bars. This is not a Lien Ch. 8–16 entry engine.
"""

from __future__ import annotations

from typing import Any

from agent.schema import Proposal
from app import risk as risk_lib

PLANNED_R = 2.0
BUFFER_PIPS = 10  # middle of Lien's ±5–15 pip buffer off highs/lows

_BB_KEYS = (
    ("mid", "bb_mid"),
    ("upper_1", "bb_upper_1"),
    ("upper_2", "bb_upper_2"),
    ("lower_1", "bb_lower_1"),
    ("lower_2", "bb_lower_2"),
)


def pip_size(instrument: str) -> float:
    """JPY-quoted pairs use 0.01; other FX uses 0.0001."""
    text = (instrument or "").strip().upper().replace("/", "_")
    quote = text.split("_")[-1] if "_" in text else text[-3:]
    return 0.01 if quote == "JPY" else 0.0001


def _round_pip(price: float, pip: float) -> float:
    return round(price / pip) * pip


def named_levels(analysis: dict[str, Any]) -> dict[str, float]:
    """Extract numeric Ch. 7 levels from ``analyze_bars`` output."""
    snap = analysis.get("snapshot") or {}
    out: dict[str, float] = {}
    close = snap.get("last_close")
    if close is None:
        close = analysis.get("last_close")
    if close is not None:
        out["last_close"] = float(close)
    sma = snap.get("sma") or {}
    for period in (10, 20, 50, 100, 200):
        value = sma.get(str(period))
        if value is not None:
            out[f"sma_{period}"] = float(value)
    bb = snap.get("bollinger") or {}
    for src, dest in _BB_KEYS:
        value = bb.get(src)
        if value is not None:
            out[dest] = float(value)
    if snap.get("high_n") is not None:
        out["high_n"] = float(snap["high_n"])
    if snap.get("low_n") is not None:
        out["low_n"] = float(snap["low_n"])
    return out


def _mid(levels: dict[str, float]) -> float | None:
    if "bb_mid" in levels:
        return levels["bb_mid"]
    if "sma_20" in levels:
        return levels["sma_20"]
    if "high_n" in levels and "low_n" in levels:
        return (levels["high_n"] + levels["low_n"]) / 2.0
    return None


def build_ticket(
    side: str,
    entry: float,
    stop: float,
    pip: float,
    entry_name: str,
    stop_name: str,
) -> dict[str, Any] | None:
    """Round to pip, enforce >= PLANNED_R, return a ticket dict or None.

    Shared by Ch. 7 geometry and the Ch. 8 entry engine so both use the same
    pip-rounding and minimum-R guardrails.
    """
    entry = _round_pip(entry, pip)
    stop = _round_pip(stop, pip)
    if side == "long" and not (stop < entry):
        return None
    if side == "short" and not (stop > entry):
        return None
    dist = abs(entry - stop)
    if dist < pip:
        return None
    sign = 1.0 if side == "long" else -1.0
    target = _round_pip(entry + sign * PLANNED_R * dist, pip)
    try:
        planned = risk_lib.r_multiple(entry, stop, target)
    except risk_lib.RiskError:
        return None
    if planned < PLANNED_R:
        target = _round_pip(target + sign * pip, pip)
        try:
            planned = risk_lib.r_multiple(entry, stop, target)
        except risk_lib.RiskError:
            return None
        if planned < PLANNED_R:
            return None
    return {
        "side": side,
        "entry": entry,
        "stop": stop,
        "target": target,
        "entry_name": entry_name,
        "stop_name": stop_name,
    }


def _join_trend(levels: dict[str, float], direction: str | None, pip: float, buffer: float) -> dict[str, Any] | None:
    close = levels.get("last_close")
    if close is None or direction not in ("up", "down"):
        return None
    if direction == "up":
        if "low_n" in levels:
            rail, stop_name = levels["low_n"], "low_n"
        elif "bb_lower_1" in levels:
            rail, stop_name = levels["bb_lower_1"], "bb_lower_1"
        else:
            return None
        return build_ticket("long", close, rail - buffer, pip, "last_close", stop_name)
    if "high_n" in levels:
        rail, stop_name = levels["high_n"], "high_n"
    elif "bb_upper_1" in levels:
        rail, stop_name = levels["bb_upper_1"], "bb_upper_1"
    else:
        return None
    return build_ticket("short", close, rail + buffer, pip, "last_close", stop_name)


def _fade_range(levels: dict[str, float], pip: float, buffer: float) -> dict[str, Any] | None:
    close = levels.get("last_close")
    if close is None:
        return None
    high = levels.get("high_n", levels.get("bb_upper_1"))
    low = levels.get("low_n", levels.get("bb_lower_1"))
    mid = _mid(levels)
    if high is None or low is None:
        return None
    fade_short = close >= mid if mid is not None else abs(high - close) <= abs(close - low)
    high_name = "high_n" if "high_n" in levels else "bb_upper_1"
    low_name = "low_n" if "low_n" in levels else "bb_lower_1"
    if fade_short:
        return build_ticket("short", close, high + buffer, pip, "last_close", high_name)
    return build_ticket("long", close, low - buffer, pip, "last_close", low_name)


def plan_ticket(
    analysis: dict[str, Any],
    play_class: str,
    instrument: str,
    buffer_pips: int = BUFFER_PIPS,
) -> dict[str, Any] | None:
    """Return a side/entry/stop/target dict, or None if geometry cannot ticket."""
    if play_class not in ("join_trend", "fade_range"):
        return None
    if analysis.get("trend_waning"):
        return None
    levels = named_levels(analysis)
    pip = pip_size(instrument)
    buffer = buffer_pips * pip
    if play_class == "join_trend":
        return _join_trend(levels, analysis.get("direction"), pip, buffer)
    return _fade_range(levels, pip, buffer)


def _merge_notes(existing: str, extra: str) -> str:
    text = (existing or "").strip()
    return f"{text}; {extra}" if text else extra


def apply_geometry(
    proposal: Proposal | None,
    analysis: dict[str, Any],
    instrument: str,
) -> Proposal | None:
    """Overwrite entry/stop/target from Ch. 7 snapshot. Never keep model prices."""
    if proposal is None:
        return None
    ticket = plan_ticket(analysis, proposal.play_class, instrument)
    if ticket is None:
        reason = (
            "ch7 geometry: breakout_watch, no ticket"
            if proposal.play_class == "breakout_watch"
            else "ch7 geometry: no ticket"
        )
        return proposal.model_copy(
            update={
                "side": "none",
                "entry": None,
                "stop": None,
                "target": None,
                "at_time": analysis.get("last_time") or proposal.at_time,
                "notes": _merge_notes(proposal.notes, reason),
            }
        )
    note = (
        f"ch7 geometry: {ticket['side']} entry={ticket['entry_name']} "
        f"stop={ticket['stop_name']} buffer={BUFFER_PIPS}pip target={PLANNED_R:.0f}R"
    )
    return proposal.model_copy(
        update={
            "side": ticket["side"],
            "entry": ticket["entry"],
            "stop": ticket["stop"],
            "target": ticket["target"],
            "at_time": analysis.get("last_time") or proposal.at_time,
            "notes": _merge_notes(proposal.notes, note),
        }
    )
