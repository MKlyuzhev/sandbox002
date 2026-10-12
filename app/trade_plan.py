"""Plans: a pre-trade analysis with an order spec, turned into an order ticket.

The dashboard shows the exact MT4 order (type, price, SL, TP, lots for a risk
amount, expiry) and the plan's live checks; the trader enters it in MT4 by
hand. Nothing here places, modifies, or closes orders.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from app import mt4_bridge, mtf_channel
from app.trader_store import ORDER_TYPES

CHECK_RULES = ("close_below", "close_above")
LOT_STEP = 0.01
CONTRACT = 100_000


class PlanError(ValueError):
    pass


def _side(order_type: str) -> str:
    return order_type.split("_")[0]


def _broker_ts(value: Any) -> int | None:
    """Broker time as unix seconds; accepts an int or ``YYYY-MM-DD HH:MM``."""
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        dt = datetime.strptime(str(value).strip()[:16], "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise PlanError(f"expiry {value!r}: use broker time 'YYYY-MM-DD HH:MM'") from exc
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def validate_order(order: dict[str, Any]) -> dict[str, Any]:
    """Normalize an order spec; SL and TP must sit on the right side of the price."""
    otype = str(order.get("type") or "").strip().lower()
    if otype not in ORDER_TYPES:
        raise PlanError(f"order type must be one of {ORDER_TYPES}")
    try:
        price, sl, tp = (float(order[k]) for k in ("price", "sl", "tp"))
    except (KeyError, TypeError, ValueError) as exc:
        raise PlanError("order needs numeric price, sl and tp") from exc
    if _side(otype) == "buy" and not sl < price < tp:
        raise PlanError("a buy order needs sl < price < tp")
    if _side(otype) == "sell" and not tp < price < sl:
        raise PlanError("a sell order needs tp < price < sl")
    out: dict[str, Any] = {"type": otype, "price": price, "sl": sl, "tp": tp,
                           "expiry": _broker_ts(order.get("expiry"))}
    risk = order.get("risk")
    if risk:
        try:
            amount = float(risk["amount"])
            ccy = str(risk["ccy"]).strip().upper()
        except (KeyError, TypeError, ValueError) as exc:
            raise PlanError("risk needs amount and ccy") from exc
        if amount <= 0 or len(ccy) != 3:
            raise PlanError("risk amount must be > 0 and ccy a 3-letter code")
        out["risk"] = {"amount": amount, "ccy": ccy}
    checks = []
    for c in order.get("checks") or []:
        tf = mtf_channel.get_tf(str(c.get("timeframe") or ""))
        line = str(c.get("line") or "")
        rule = str(c.get("rule") or "")
        leg = str(c.get("leg") or "current")
        if line not in mtf_channel.ROLES:
            raise PlanError(f"check line must be one of {mtf_channel.ROLES}")
        if rule not in CHECK_RULES:
            raise PlanError(f"check rule must be one of {CHECK_RULES}")
        if leg not in ("current", "previous"):
            raise PlanError("check leg must be 'current' or 'previous'")
        checks.append({"label": str(c.get("label") or f"{tf.name} {line} {rule}"),
                       "timeframe": tf.name, "leg": leg, "line": line, "rule": rule})
    out["checks"] = checks
    return out


# ---------------------------------------------------------------- sizing


def _mid(price: dict[str, Any]) -> float:
    return (float(price["bids"][0]["price"]) + float(price["asks"][0]["price"])) / 2


def conversion_pairs(instrument: str, account_ccy: str) -> list[str]:
    """OANDA instruments whose prices convert ``instrument``'s quote currency."""
    base, quote = instrument.upper().split("_")
    ccy = account_ccy.upper()
    if ccy in (base, quote):
        return [instrument.upper()]
    return [instrument.upper(), f"{quote}_{ccy}", f"{ccy}_{quote}"]


def quote_to_account(instrument: str, account_ccy: str, mids: dict[str, float]) -> float:
    """Account-currency value of one unit of the quote currency."""
    base, quote = instrument.upper().split("_")
    ccy = account_ccy.upper()
    if quote == ccy:
        return 1.0
    if base == ccy:
        return 1.0 / mids[instrument.upper()]
    if f"{quote}_{ccy}" in mids:
        return mids[f"{quote}_{ccy}"]
    if f"{ccy}_{quote}" in mids:
        return 1.0 / mids[f"{ccy}_{quote}"]
    raise PlanError(f"no price to convert {quote} to {ccy}")


def size(
    instrument: str, order: dict[str, Any], risk_amount: float, account_ccy: str,
    mids: dict[str, float],
) -> dict[str, Any]:
    """Lots for ``risk_amount`` (account currency) at the plan's stop, rounded down."""
    pip = mtf_channel.pip_size(instrument)
    stop_pips = abs(order["price"] - order["sl"]) / pip
    target_pips = abs(order["tp"] - order["price"]) / pip
    pip_value = CONTRACT * pip * quote_to_account(instrument, account_ccy, mids)  # per 1.00 lot
    lots = math.floor(risk_amount / (stop_pips * pip_value) / LOT_STEP + 1e-9) * LOT_STEP
    lots = round(lots, 2)
    return {
        "account_ccy": account_ccy.upper(),
        "risk_amount": risk_amount,
        "stop_pips": round(stop_pips, 1),
        "target_pips": round(target_pips, 1),
        "r_multiple": round(target_pips / stop_pips, 2),
        "pip_value_per_lot": round(pip_value, 4),
        "lots": lots,
        "risk_at_lots": round(lots * stop_pips * pip_value, 2),
        "reward_at_lots": round(lots * target_pips * pip_value, 2),
        "too_small": lots < LOT_STEP,
    }


async def live_mids(instruments: list[str]) -> dict[str, float]:
    """Mid prices for the instruments OANDA knows (unknown crosses are skipped)."""
    from app import oanda_client

    out: dict[str, float] = {}
    for name in dict.fromkeys(instruments):
        try:
            prices = await oanda_client.get_pricing(name)
        except Exception:  # noqa: BLE001 - a missing cross is not an error
            continue
        if prices:
            out[name] = _mid(prices[0])
    return out


async def quote(instrument: str) -> dict[str, Any]:
    from app import oanda_client

    p = (await oanda_client.get_pricing(instrument.upper()))[0]
    return {"bid": float(p["bids"][0]["price"]), "ask": float(p["asks"][0]["price"]),
            "time": p.get("time")}


def distance_pips(instrument: str, order: dict[str, Any], bid: float, ask: float) -> float:
    """How far the trigger price is: sells trigger on the bid, buys on the ask."""
    ref = bid if _side(order["type"]) == "sell" else ask
    return round((order["price"] - ref) / mtf_channel.pip_size(instrument), 1)


# ---------------------------------------------------------------- checks


async def _grid_for(instrument: str, check: dict[str, Any]) -> tuple[list[dict], mtf_channel.Grid]:
    from app import trader_outbox

    tf = mtf_channel.get_tf(check["timeframe"])
    if tf.origin != "legs":
        return await mtf_channel.w1_grid(instrument)
    bars = (await mtf_channel.load_bars(instrument, tf))["bars"]
    proposals = await mtf_channel.leg_proposals_for(instrument, tf, bars)
    objects = trader_outbox.load_chart(mt4_bridge.chart_dir(instrument, tf.name)).get("objects") or []
    name = mtf_channel.leg_channel_name(instrument, proposals, check["leg"])
    if name in {o.get("name") for o in objects if o.get("type") == "channel"}:
        grid = mtf_channel.grid_from_chart(bars, objects, name, tf)
        grid.source = f"chart: {name}"
    else:
        grid = mtf_channel.grid_from_leg(bars, proposals, check["leg"])
        grid.source = f"proposal: {grid.source}"
    return bars, grid


async def run_checks(instrument: str, order: dict[str, Any]) -> list[dict[str, Any]]:
    """Each check against the last completed close of its timeframe."""
    out = []
    for c in order.get("checks") or []:
        row = dict(c)
        try:
            bars, grid = await _grid_for(instrument, c)
            done = [b for b in bars if b.get("complete", True)]
            last = len(done) - 1
            line_last = grid.lines[c["line"]].at(last)
            close = done[last]["close"]
            ok = close < line_last if c["rule"] == "close_below" else close > line_last
            digits = 3 if "JPY" in instrument.upper() else 5  # MT4 price digits
            row.update(
                ok=ok, close=close, line_at_close=round(line_last, digits),
                line_next=round(grid.lines[c["line"]].at(last + 1), digits),
                bar=mtf_channel._date(done[last]["time"], grid.tf), source=grid.source,
            )
        except (mtf_channel.ChannelError, mt4_bridge.Mt4BridgeError, OSError, ValueError) as exc:
            row.update(ok=None, error=str(exc))
        out.append(row)
    return out


# ---------------------------------------------------------------- ticket


def fmt_broker(ts: int | None) -> str:
    if not ts:
        return "none"
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y.%m.%d %H:%M")


def ticket_text(instrument: str, order: dict[str, Any], lots: float | None) -> str:
    """The MT4 order as the trader types it (F9 → Pending Order)."""
    sym = mt4_bridge.map_symbol(instrument)
    kind = " ".join(w.capitalize() for w in order["type"].split("_"))
    lines = [
        f"{sym}  {kind}",
        f"At price     {order['price']}",
        f"Stop Loss    {order['sl']}",
        f"Take Profit  {order['tp']}",
        f"Volume       {lots if lots is not None else '-'}",
        f"Expiry       {fmt_broker(order.get('expiry'))} (broker)",
        "",
        f"MT4: F9 -> Type: Pending Order -> {kind} -> fill the fields -> Place",
    ]
    return "\n".join(lines)


def to_instrument(symbol: str) -> str:
    """MT4 ``USDJPY`` -> OANDA ``USD_JPY``."""
    s = symbol.replace("_", "").upper()
    if len(s) != 6:
        raise PlanError(f"cannot map {symbol!r} to an OANDA instrument")
    return f"{s[:3]}_{s[3:]}"


async def ticket(
    row: dict[str, Any], risk_amount: float | None = None, account_ccy: str | None = None
) -> dict[str, Any]:
    """The order ticket for a plan: sizing for the risk (else the plan's
    default), live distance to the trigger, and the text to type into MT4."""
    order = row["order"]
    instrument = to_instrument(row["symbol"])
    risk = order.get("risk") or {}
    amount = risk_amount if risk_amount else risk.get("amount")
    ccy = (account_ccy or risk.get("ccy") or "USD").upper()
    out: dict[str, Any] = {"instrument": instrument, "order": order, "sizing": None,
                           "price": None, "expiry": fmt_broker(order.get("expiry"))}
    mids = await live_mids(conversion_pairs(instrument, ccy))
    if amount:
        try:
            out["sizing"] = size(instrument, order, float(amount), ccy, mids)
        except (PlanError, KeyError, ZeroDivisionError) as exc:
            out["sizing_error"] = str(exc)
    try:
        q = await quote(instrument)
        q["distance_pips"] = distance_pips(instrument, order, q["bid"], q["ask"])
        out["price"] = q
    except Exception as exc:  # noqa: BLE001 - the ticket stands without a live price
        out["price_error"] = str(exc)
    lots = out["sizing"]["lots"] if out["sizing"] else None
    out["text"] = ticket_text(instrument, order, lots)
    return out
