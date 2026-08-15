"""Causal walk-forward Lien regime labels.

Absolute rule: a label at bar ``i`` uses only ``bars[: i + 1][-lookback:]``.
No centered window, no right padding, no future bars. Collapse is post-hoc
display of those labels (see docs/LIEN_FX_STRATEGIES.md).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app import indicators, regime


def _parse_rfc3339_utc(value: str | None) -> int | None:
    """Parse RFC3339 (possibly nanosecond) to UTC unix seconds."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "." in text:
        head, rest = text.split(".", 1)
        tz_sep = "+" if "+" in rest else ("-" if "-" in rest[1:] else "")
        if tz_sep:
            idx = rest.find(tz_sep, 1) if tz_sep == "-" else rest.find(tz_sep)
            frac, tz = rest[:idx], rest[idx:]
        else:
            frac, tz = rest, "+00:00"
        frac = "".join(c for c in frac if c.isdigit())[:6].ljust(6, "0")
        text = f"{head}.{frac}{tz}"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


DEFAULT_LOOKBACK = 250
MAX_BARS = 5000
DEFAULT_HORIZON = 5
DEFAULT_MIN_N = 10
DEFAULT_PHAT_WATCH = 0.5
DEFAULT_INSTABILITY_WATCH = 0.6

# Instability score weights (sum to 1.0). Risk-of-flip, not a probability.
_W_MIXED_OR_WANING = 0.25
_W_ADX_BOUNDARY = 0.25
_W_X_GAP = 0.20
_W_PERFECT_ORDER_LOST = 0.15
_W_LOW_CONFIDENCE = 0.15


class WalkError(ValueError):
    """Raised when walk inputs violate a causal or size invariant."""


def drop_incomplete(bars: list[dict]) -> list[dict]:
    """Keep bars that are complete (missing ``complete`` is treated as True)."""
    return [b for b in bars if b.get("complete", True)]


def drop_after(bars: list[dict], to_time: str | None) -> list[dict]:
    """Drop bars strictly after ``to_time`` (classification must not use them)."""
    if not to_time:
        return list(bars)
    end = _parse_rfc3339_utc(to_time)
    if end is None:
        raise WalkError(f"invalid to_time: {to_time!r}")
    out: list[dict] = []
    for b in bars:
        ts = _parse_rfc3339_utc(b.get("time"))
        if ts is None:
            continue
        if ts <= end:
            out.append(b)
    return out


def first_index_on_or_after(bars: list[dict], from_time: str) -> int:
    """Index of the first bar with time >= ``from_time``."""
    start = _parse_rfc3339_utc(from_time)
    if start is None:
        raise WalkError(f"invalid from_time: {from_time!r}")
    for i, b in enumerate(bars):
        ts = _parse_rfc3339_utc(b.get("time"))
        if ts is not None and ts >= start:
            return i
    raise WalkError(f"no complete bar on or after {from_time}")


def _dedupe_by_time(bars: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for b in bars:
        t = b.get("time")
        key = str(t) if t is not None else ""
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(b)
    return out


def prepare_bars(
    bars: list[dict],
    to_time: str | None = None,
) -> list[dict]:
    """Complete bars only, not after ``to_time``, unique times, chronological."""
    cleaned = drop_after(drop_incomplete(bars), to_time)
    return _dedupe_by_time(cleaned)


def walk(
    bars: list[dict],
    lookback: int = DEFAULT_LOOKBACK,
    step: int = 1,
    start_index: int | None = None,
) -> list[dict[str, Any]]:
    """Label each right-edge bar with a causal sliding window.

    Window at index ``i`` is exactly ``bars[: i + 1][-lookback:]``.
    ``start_index`` is the first right-edge to label (default ``lookback - 1``).
    """
    if lookback < indicators.MIN_BARS:
        raise WalkError(f"lookback must be >= {indicators.MIN_BARS}; got {lookback}")
    if step < 1:
        raise WalkError("step must be >= 1")
    series = drop_incomplete(bars)
    if start_index is None:
        start_index = lookback - 1
    if start_index < lookback - 1:
        raise WalkError(
            f"start_index {start_index} needs {lookback} bars of history "
            f"(warmup before the test start)"
        )
    if start_index >= len(series):
        raise WalkError("start_index is past the last complete bar")

    steps: list[dict[str, Any]] = []
    i = start_index
    while i < len(series):
        window = series[: i + 1][-lookback:]
        analysis = regime.analyze_bars(window)
        bar = series[i]
        steps.append(
            {
                "index": i,
                "time": bar.get("time"),
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"],
                "regime": analysis["regime"],
                "direction": analysis["direction"],
                "trend_waning": analysis["trend_waning"],
                "confidence": analysis["confidence"],
                "allowed_play_classes": analysis["allowed_play_classes"],
                "ma_perfect_order": analysis["ma_perfect_order"],
                "trend_x_count": analysis["trend_x_count"],
                "range_x_count": analysis["range_x_count"],
                "adx": (analysis.get("snapshot") or {}).get("adx"),
            }
        )
        i += step
    return steps


def collapse_runs(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge consecutive equal (regime, direction). High/low from run steps only."""
    runs: list[dict[str, Any]] = []
    for step in steps:
        key = (step.get("regime"), step.get("direction"))
        if runs and (runs[-1]["regime"], runs[-1]["direction"]) == key:
            run = runs[-1]
            run["end_index"] = step["index"]
            run["end_time"] = step.get("time")
            run["high"] = max(float(run["high"]), float(step["high"]))
            run["low"] = min(float(run["low"]), float(step["low"]))
            run["bar_count"] += 1
            if step.get("trend_waning"):
                run["trend_waning"] = True
            continue
        runs.append(
            {
                "regime": step.get("regime"),
                "direction": step.get("direction"),
                "trend_waning": bool(step.get("trend_waning")),
                "start_index": step["index"],
                "end_index": step["index"],
                "start_time": step.get("time"),
                "end_time": step.get("time"),
                "high": float(step["high"]),
                "low": float(step["low"]),
                "bar_count": 1,
            }
        )
    return runs


def _adx_value(step: dict[str, Any]) -> float | None:
    adx = step.get("adx") or {}
    if not isinstance(adx, dict):
        return None
    raw = adx.get("adx")
    try:
        return None if raw is None else float(raw)
    except (TypeError, ValueError):
        return None


def adx_band(step: dict[str, Any]) -> str:
    value = _adx_value(step)
    if value is None:
        return "unknown"
    if value < 20:
        return "lt20"
    if value <= 30:
        return "20_30"
    return "gt30"


def state_bucket(step: dict[str, Any]) -> tuple[Any, bool, str]:
    return (step.get("regime"), bool(step.get("trend_waning")), adx_band(step))


def regime_changed(earlier: dict[str, Any], later: dict[str, Any]) -> bool:
    """True if regime label or trend direction flipped between two causal steps."""
    if earlier.get("regime") != later.get("regime"):
        return True
    if earlier.get("regime") == "trend" and earlier.get("direction") != later.get(
        "direction"
    ):
        return True
    return False


def instability_score(
    step: dict[str, Any],
    prev: dict[str, Any] | None = None,
) -> float:
    """Weighted 0–1 risk-of-flip from current (and previous) checklist fields."""
    score = 0.0
    if step.get("regime") == "mixed" or step.get("trend_waning"):
        score += _W_MIXED_OR_WANING
    adx = step.get("adx") or {}
    adx_v = _adx_value(step)
    slope = adx.get("slope") if isinstance(adx, dict) else None
    rising = bool(adx.get("rising")) if isinstance(adx, dict) else False
    falling = slope is not None and float(slope) < 0
    near_boundary = adx_v is not None and 20.0 <= adx_v <= 30.0
    slope_against = (step.get("regime") == "trend" and falling) or (
        step.get("regime") == "range" and rising
    )
    if near_boundary or slope_against:
        score += _W_ADX_BOUNDARY
    trend_n = step.get("trend_x_count")
    range_n = step.get("range_x_count")
    if trend_n is not None and range_n is not None and abs(int(trend_n) - int(range_n)) <= 1:
        score += _W_X_GAP
    if prev is not None:
        prev_order = prev.get("ma_perfect_order")
        cur_order = step.get("ma_perfect_order")
        if prev_order in ("up", "down") and cur_order != prev_order:
            score += _W_PERFECT_ORDER_LOST
    confidence = step.get("confidence")
    if confidence is not None and float(confidence) < 0.5:
        score += _W_LOW_CONFIDENCE
    return round(min(score, 1.0), 3)


def attach_change_prob(
    steps: list[dict[str, Any]],
    horizon: int = DEFAULT_HORIZON,
    min_n: int = DEFAULT_MIN_N,
) -> list[dict[str, Any]]:
    """Attach causal instability, p_hat, and delayed eval. Mutates ``steps``.

    ``p_hat`` at list index ``i`` uses only episodes ``j`` with ``j + horizon <= i``.
    Eval at ``i`` scores the forecast made at ``i - horizon`` (outcome now known).
    """
    if horizon < 1:
        raise WalkError("horizon must be >= 1")
    if min_n < 1:
        raise WalkError("min_n must be >= 1")

    for i, step in enumerate(steps):
        prev = steps[i - 1] if i > 0 else None
        step["instability"] = instability_score(step, prev)
        step["bucket"] = list(state_bucket(step))
        step["p_hat"] = None
        step["n_hist"] = 0
        step["p_hat_note"] = "insufficient_history"
        step["eval_changed"] = None
        step["eval_p_hat"] = None
        step["eval_brier"] = None

    for i, step in enumerate(steps):
        bucket = tuple(step["bucket"])
        n = 0
        hits = 0
        for j in range(i):
            if j + horizon > i:
                continue
            if tuple(steps[j]["bucket"]) != bucket:
                continue
            n += 1
            if regime_changed(steps[j], steps[j + horizon]):
                hits += 1
        step["n_hist"] = n
        if n >= min_n:
            step["p_hat"] = round(hits / n, 4)
            step["p_hat_note"] = None
        k = i - horizon
        if k >= 0:
            step["eval_changed"] = regime_changed(steps[k], step)
            step["eval_p_hat"] = steps[k].get("p_hat")
            if step["eval_p_hat"] is not None:
                y = 1.0 if step["eval_changed"] else 0.0
                step["eval_brier"] = round((float(step["eval_p_hat"]) - y) ** 2, 4)
    return steps


def summarize(steps: list[dict[str, Any]], runs: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for step in steps:
        key = str(step.get("regime") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    phats = [float(s["p_hat"]) for s in steps if s.get("p_hat") is not None]
    briers = [float(s["eval_brier"]) for s in steps if s.get("eval_brier") is not None]
    evals = [s for s in steps if s.get("eval_changed") is not None]
    hits = sum(1 for s in evals if s.get("eval_changed") and s.get("eval_p_hat") is not None)
    scored = sum(1 for s in evals if s.get("eval_p_hat") is not None)
    last = steps[-1] if steps else {}
    return {
        "step_count": len(steps),
        "run_count": len(runs),
        "regime_counts": counts,
        "mean_p_hat": round(sum(phats) / len(phats), 4) if phats else None,
        "p_hat_defined": len(phats),
        "completed_forecasts": scored,
        "brier": round(sum(briers) / len(briers), 4) if briers else None,
        "hit_rate": round(hits / scored, 4) if scored else None,
        "last_p_hat": last.get("p_hat"),
        "last_instability": last.get("instability"),
    }


def walk_and_collapse(
    bars: list[dict],
    lookback: int = DEFAULT_LOOKBACK,
    step: int = 1,
    start_index: int | None = None,
    horizon: int = DEFAULT_HORIZON,
    min_n: int = DEFAULT_MIN_N,
) -> dict[str, Any]:
    steps = walk(bars, lookback=lookback, step=step, start_index=start_index)
    attach_change_prob(steps, horizon=horizon, min_n=min_n)
    runs = collapse_runs(steps)
    return {
        "steps": steps,
        "runs": runs,
        "summary": summarize(steps, runs),
        "horizon": horizon,
        "min_n": min_n,
    }
