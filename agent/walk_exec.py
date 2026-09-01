"""REST-shaped fill / exit helpers for causal paper walks.

``--fill close`` (default) still fills at the decision-bar mid close and
compounds equity via ``apply_r_to_equity``. ``--fill rest`` waits for the next
bar, fills at the taking-side open (long ask / short bid), sizes with integer
``position_size`` units, and exits on the making side. Research-only simulator;
not broker P&L and not ``POST /orders``.
"""

from __future__ import annotations

import argparse
import math
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from agent.schema import Goal, PaperTrade
from app import risk as risk_lib
from app.regime_walk import WalkError

FillMode = Literal["close", "rest"]


@dataclass
class RestFill:
    price: float
    units: int
    quote: Literal["ask", "bid"]


@dataclass
class RestPending:
    """Policy-approved ticket waiting for the next bar's taking-side open."""

    fill_index: int
    side: str
    play_class: str
    stop: float
    target: float
    reasons: list[str]
    proposal: Any
    verdict: Any
    analysis: dict[str, Any]
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)


def fill_mode_of(goal: Goal) -> FillMode:
    mode = getattr(goal, "fill_mode", "close") or "close"
    if mode not in ("close", "rest"):
        raise WalkError(f"unknown fill_mode {mode!r}")
    return mode  # type: ignore[return-value]


def add_fill_cli_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--fill",
        dest="fill_mode",
        choices=("close", "rest"),
        default="close",
        help=(
            "close: fill at decision-bar mid close, equity via R (default). "
            "rest: next-bar bid/ask open like a REST market order; integer "
            "units from position_size. USD-quoted pairs assume "
            "--value-per-price-unit 1.0; set it for USD_JPY etc."
        ),
    )
    parser.add_argument(
        "--value-per-price-unit",
        type=float,
        default=1.0,
        help=(
            "Home-currency P&L per unit per 1.0 of price (default 1.0 = "
            "USD-quoted in a USD account). Needed for rest fills on pairs "
            "like USD_JPY."
        ),
    )


def ba_ohlc(bar: dict[str, Any], which: Literal["bid", "ask"]) -> dict[str, float]:
    raw = bar.get(which)
    if not isinstance(raw, dict):
        raise WalkError(f"rest fill requires bar[{which!r}] OHLC; no mid fallback")
    try:
        return {
            "o": float(raw["o"]),
            "h": float(raw["h"]),
            "l": float(raw["l"]),
            "c": float(raw["c"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise WalkError(
            f"rest fill requires bar[{which!r}] keys o/h/l/c; no mid fallback"
        ) from exc


def rest_fill_price(side: str, next_bar: dict[str, Any]) -> float:
    if side == "long":
        return ba_ohlc(next_bar, "ask")["o"]
    if side == "short":
        return ba_ohlc(next_bar, "bid")["o"]
    raise WalkError(f"rest fill needs long or short; got {side!r}")


def fill_through_stop(side: str, fill: float, stop: float) -> bool:
    if side == "long":
        return fill <= stop
    if side == "short":
        return fill >= stop
    raise WalkError(f"fill_through_stop needs long or short; got {side!r}")


def rest_units(
    equity: float,
    risk_fraction: float,
    fill: float,
    stop: float,
    value_per_price_unit: float,
) -> int | None:
    distance = abs(fill - stop)
    if distance <= 0:
        return None
    try:
        raw = risk_lib.position_size(
            equity, risk_fraction, distance, value_per_price_unit
        )
    except risk_lib.RiskError:
        return None
    units = math.floor(raw)
    if units < 1:
        return None
    return units


def rest_pnl(
    side: str,
    fill: float,
    exit_price: float,
    units: int,
    value_per_price_unit: float,
) -> float:
    if side == "long":
        return units * (exit_price - fill) * value_per_price_unit
    if side == "short":
        return units * (fill - exit_price) * value_per_price_unit
    raise WalkError(f"rest_pnl needs long or short; got {side!r}")


def try_rest_fill(
    side: str,
    stop: float,
    fill_bar: dict[str, Any],
    equity: float,
    risk_fraction: float,
    value_per_price_unit: float,
) -> RestFill | None:
    """Next-bar taking-side open, or None if gapped through the stop / unsizable."""
    price = rest_fill_price(side, fill_bar)
    if fill_through_stop(side, price, stop):
        return None
    units = rest_units(equity, risk_fraction, price, stop, value_per_price_unit)
    if units is None:
        return None
    quote: Literal["ask", "bid"] = "ask" if side == "long" else "bid"
    return RestFill(price=price, units=units, quote=quote)


def check_exit_rest(
    side: str, stop: float, target: float, bar: dict[str, Any]
) -> tuple[str, float] | None:
    """Making-side exit. Gap through the stop exits at that open, not the stop.

    Long uses bid H/L; short uses ask H/L. Stop still wins if both trade
    after a non-gapped open.
    """
    making = ba_ohlc(bar, "bid" if side == "long" else "ask")
    open_px = making["o"]
    high = making["h"]
    low = making["l"]
    if side == "long":
        if open_px <= stop:
            return "stop", open_px
        if low <= stop:
            return "stop", stop
        if high >= target:
            return "target", target
    elif side == "short":
        if open_px >= stop:
            return "stop", open_px
        if high >= stop:
            return "stop", stop
        if low <= target:
            return "target", target
    else:
        raise WalkError(f"check_exit_rest needs long or short; got {side!r}")
    return None


def may_check_exit(entry_index: int, bar_index: int, fill_mode: FillMode) -> bool:
    if fill_mode == "rest":
        return bar_index >= entry_index
    return bar_index > entry_index


def window_end_price(bar: dict[str, Any], side: str, fill_mode: FillMode) -> float:
    if fill_mode == "rest":
        return ba_ohlc(bar, "bid" if side == "long" else "ask")["c"]
    return float(bar["close"])


def rest_journal_note(side: str, units: int) -> str:
    quote = "ask" if side == "long" else "bid"
    return f"walk fill rest next-open {quote}; units={units}"


def paper_trade_from_rest(
    pending: RestPending,
    fill: RestFill,
    fill_index: int,
    fill_time: str,
    walk_id: str,
) -> PaperTrade:
    return PaperTrade(
        run_id=pending.run_id,
        entry_index=fill_index,
        entry_time=fill_time,
        side=pending.side,  # type: ignore[arg-type]
        play_class=pending.play_class,  # type: ignore[arg-type]
        entry=fill.price,
        stop=pending.stop,
        target=pending.target,
        reasons=list(pending.reasons),
        walk_id=walk_id,
        units=fill.units,
    )


def realize_rest_trade(
    pending: RestPending,
    fill_bar: dict[str, Any],
    fill_index: int,
    fill_time: str,
    equity: float,
    goal: Goal,
    walk_id: str,
) -> tuple[PaperTrade, RestFill] | None:
    fill = try_rest_fill(
        pending.side,
        pending.stop,
        fill_bar,
        equity,
        goal.risk_fraction,
        goal.value_per_price_unit,
    )
    if fill is None:
        return None
    trade = paper_trade_from_rest(pending, fill, fill_index, fill_time, walk_id)
    return trade, fill
