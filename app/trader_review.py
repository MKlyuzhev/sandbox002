"""Code-scored post-trade review vs the trader's frozen marks."""

from __future__ import annotations

from typing import Any

from app.mt4_bridge import parse_rfc3339_utc
from app.trader_episodes import pip_size

LOOKFORWARD_BARS = 12


def _unix(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    parsed = parse_rfc3339_utc(str(value))
    return int(parsed) if parsed else 0


def _line_price(t: int, t1: int, p1: float, t2: int, p2: float) -> float:
    if t2 == t1:
        return p1
    frac = (t - t1) / float(t2 - t1)
    return p1 + frac * (p2 - p1)


def _channel_rails(obj: dict[str, Any], t: int) -> tuple[float, float] | None:
    t1 = _unix(obj.get("t1"))
    t2 = _unix(obj.get("t2"))
    p1 = float(obj.get("p1") or 0)
    p2 = float(obj.get("p2") or 0)
    if not t1:
        return None
    if not t2:
        t2 = t1
    main = _line_price(t, t1, p1, t2, p2)
    t3 = _unix(obj.get("t3"))
    p3 = obj.get("p3")
    if t3 and p3 is not None:
        offset = float(p3) - _line_price(t3, t1, p1, t2, p2)
        other = main + offset
        return (min(main, other), max(main, other))
    return (min(p1, p2), max(p1, p2)) if p1 and p2 else (main, main)


def _structure_objects(objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for obj in objects:
        role = str(obj.get("role") or "unknown")
        typ = str(obj.get("type") or "")
        if role in {"sr", "channel", "entry", "stop", "target", "pattern"}:
            out.append(obj)
            continue
        if role == "unknown" and typ in {
            "hline",
            "trend",
            "channel",
            "rectangle",
            "fibo",
        }:
            out.append(obj)
    return out


def _bars_in_window(
    bars: list[dict[str, Any]], start: int, end: int
) -> list[dict[str, Any]]:
    kept = []
    for bar in bars:
        ts = _unix(bar.get("time"))
        if start <= ts <= end:
            kept.append(bar)
    return kept


def _mae_mfe(
    bars: list[dict[str, Any]],
    side: str,
    entry: float,
    stop: float | None,
) -> tuple[float | None, float | None]:
    if not bars or not entry:
        return None, None
    risk = abs(entry - stop) if stop else None
    if side == "long":
        mae_px = max(0.0, entry - min(float(b["low"]) for b in bars))
        mfe_px = max(0.0, max(float(b["high"]) for b in bars) - entry)
    else:
        mae_px = max(0.0, max(float(b["high"]) for b in bars) - entry)
        mfe_px = max(0.0, entry - min(float(b["low"]) for b in bars))
    if not risk:
        return mae_px, mfe_px
    return mae_px / risk, mfe_px / risk


def _inside_structure(obj: dict[str, Any], bar: dict[str, Any]) -> bool | None:
    typ = str(obj.get("type") or "")
    ts = _unix(bar.get("time"))
    close = float(bar["close"])
    if typ == "hline":
        p = float(obj.get("p1") or 0)
        return True
    if typ in {"trend", "channel"}:
        rails = _channel_rails(obj, ts)
        if not rails:
            return None
        lo, hi = rails
        return lo <= close <= hi
    if typ == "rectangle":
        lo = min(float(obj.get("p1") or 0), float(obj.get("p2") or 0))
        hi = max(float(obj.get("p1") or 0), float(obj.get("p2") or 0))
        return lo <= close <= hi
    return None


def _close_through_hline(obj: dict[str, Any], bar: dict[str, Any], pip: float) -> bool:
    p = float(obj.get("p1") or 0)
    close = float(bar["close"])
    high = float(bar["high"])
    low = float(bar["low"])
    tagged = low <= p <= high
    sliced = tagged and abs(close - p) > 2 * pip
    return sliced


def review_trade(
    *,
    order: dict[str, Any],
    objects: list[dict[str, Any]],
    bars: list[dict[str, Any]],
    lookforward_bars: int = LOOKFORWARD_BARS,
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Score model_fit, entry_quality, exit_quality. P&L is not the score."""
    reasons: list[str] = []
    diagnostics: dict[str, Any] = {}
    symbol = str(order.get("symbol") or "")
    pip = pip_size(symbol)
    otype = str(order.get("type") or "")
    side = "long" if otype.startswith("buy") else "short" if otype.startswith("sell") else "none"
    entry = float(order.get("open_price") or 0)
    stop = float(order.get("sl") or 0) or None
    target = float(order.get("tp") or 0) or None
    sl_open = float(order.get("sl_open") or 0) or stop
    tp_open = float(order.get("tp_open") or 0) or target
    open_ts = _unix(order.get("open_time"))
    close_ts = _unix(order.get("close_time")) or open_ts
    hold = _bars_in_window(bars, open_ts, close_ts)
    after = []
    if hold:
        last = _unix(hold[-1].get("time"))
        after = [b for b in bars if _unix(b.get("time")) > last][:lookforward_bars]
    elif close_ts:
        after = [b for b in bars if _unix(b.get("time")) > close_ts][:lookforward_bars]

    structure = _structure_objects(objects)
    diagnostics["structure_count"] = len(structure)
    diagnostics["hold_bars"] = len(hold)
    diagnostics["side"] = side

    model_fit: float | None
    bars_to_invalidation: int | None = None
    if not structure:
        model_fit = None
        reasons.append("no_structure")
    else:
        inside_flags: list[bool] = []
        invalid_at = None
        for i, bar in enumerate(hold):
            ok_bar = True
            for obj in structure:
                typ = str(obj.get("type") or "")
                if typ == "hline":
                    if _close_through_hline(obj, bar, pip):
                        ok_bar = False
                else:
                    inside = _inside_structure(obj, bar)
                    if inside is False:
                        ok_bar = False
            inside_flags.append(ok_bar)
            if not ok_bar and invalid_at is None:
                invalid_at = i
        if inside_flags:
            frac = sum(1 for x in inside_flags if x) / len(inside_flags)
            bars_to_invalidation = invalid_at
            model_fit = round(frac, 3)
            if invalid_at is not None and invalid_at <= 1:
                model_fit = round(min(model_fit, 0.25), 3)
                reasons.append("structure_broke_immediately")
            elif invalid_at is None:
                reasons.append("structure_held")
            else:
                reasons.append(f"invalidated_at_bar_{invalid_at}")
        else:
            model_fit = None
            reasons.append("no_hold_bars")
    diagnostics["bars_to_invalidation"] = bars_to_invalidation

    mae_r, mfe_r = _mae_mfe(hold, side, entry, sl_open)
    diagnostics["mae_r"] = mae_r
    diagnostics["mfe_r"] = mfe_r

    entry_quality = 0.7
    if not structure:
        reasons.append("entry_unscored_no_structure")
    else:
        rails = []
        for obj in structure:
            typ = str(obj.get("type") or "")
            if typ == "hline":
                rails.append(float(obj.get("p1") or 0))
            elif typ in {"trend", "channel"}:
                pair = _channel_rails(obj, open_ts)
                if pair:
                    rails.extend(pair)
            elif typ == "rectangle":
                rails.append(float(obj.get("p1") or 0))
                rails.append(float(obj.get("p2") or 0))
        if rails and entry:
            dist = min(abs(entry - r) for r in rails)
            diagnostics["entry_to_rail_pips"] = dist / pip
            if dist > 15 * pip:
                entry_quality -= 0.25
                reasons.append("chase")
            else:
                reasons.append("entry_near_rail")
        if sl_open and rails:
            stop_inside = min(rails) < sl_open < max(rails) if len(rails) >= 2 else False
            diagnostics["stop_inside_structure"] = stop_inside
            if stop_inside:
                entry_quality -= 0.2
                reasons.append("stop_inside_structure")
        if mae_r is not None and mae_r > 0.7:
            entry_quality -= 0.15
            reasons.append("large_mae_before_green")
        # Side vs nearest hline: long above a resistance-like high hline.
        hlines = [
            float(o.get("p1") or 0)
            for o in structure
            if str(o.get("type")) == "hline"
        ]
        if hlines and side == "long" and entry > max(hlines) + 5 * pip:
            reasons.append("long_above_resistance")
            entry_quality -= 0.2
        if hlines and side == "short" and entry < min(hlines) - 5 * pip:
            reasons.append("short_below_support")
            entry_quality -= 0.2
    entry_quality = round(max(0.0, min(1.0, entry_quality)), 3)

    close_price = float(order.get("close_price") or 0)
    exit_kind = "manual"
    if sl_open and close_price and abs(close_price - sl_open) <= 3 * pip:
        exit_kind = "stop"
    if tp_open and close_price and abs(close_price - tp_open) <= 3 * pip:
        exit_kind = "target"
    stop_widened = False
    if sl_open and order.get("sl"):
        cur_sl = float(order["sl"])
        if side == "long" and cur_sl < sl_open - pip:
            stop_widened = True
        if side == "short" and cur_sl > sl_open + pip:
            stop_widened = True
    diagnostics["exit_kind"] = exit_kind
    diagnostics["stop_widened"] = stop_widened
    diagnostics["library_2r_note"] = None
    if sl_open and entry and not tp_open:
        dist = abs(entry - sl_open)
        guess = entry + 2 * dist if side == "long" else entry - 2 * dist
        diagnostics["library_2r_note"] = f"library 2R would have been {guess}"
        reasons.append("no_tp_set")

    exit_quality = 0.55
    if exit_kind == "target":
        exit_quality = 0.9
        reasons.append("target_hit")
    elif exit_kind == "stop":
        exit_quality = 0.35
        reasons.append("stop_hit")
        if bars_to_invalidation is not None and bars_to_invalidation <= 1:
            exit_quality = 0.25
            reasons.append("stopped_after_model_broke")
    if stop_widened:
        exit_quality -= 0.25
        reasons.append("stop_widened")
    if mfe_r is not None and exit_kind == "manual" and mfe_r > 1.0:
        leftover = 0.0
        if after and entry:
            if side == "long":
                leftover = max(float(b["high"]) for b in after) - close_price
            else:
                leftover = close_price - min(float(b["low"]) for b in after)
            diagnostics["mfe_left_pips"] = leftover / pip
            if leftover > 10 * pip:
                exit_quality -= 0.1
                reasons.append("mfe_left_on_table")
    if bars_to_invalidation is not None and hold and bars_to_invalidation < len(hold) - 1:
        if exit_kind == "manual":
            reasons.append("exited_after_invalidation")
            exit_quality -= 0.1
    exit_quality = round(max(0.0, min(1.0, exit_quality)), 3)

    brief = (
        f"model_fit={model_fit} entry={entry_quality} exit={exit_quality} "
        f"kind={exit_kind} mae_r={mae_r} mfe_r={mfe_r}"
    )
    _ = events  # order_modify events reserved for later refine
    return {
        "model_fit": model_fit,
        "entry_quality": entry_quality,
        "exit_quality": exit_quality,
        "reasons": reasons,
        "diagnostics": diagnostics,
        "brief": brief,
    }
