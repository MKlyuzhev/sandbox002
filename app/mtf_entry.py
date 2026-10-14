"""One read for the MTF entry workflow: grids, zones, plans and exposure.

Everything the ``/mtf-entry`` skill (docs/MTF_ENTRY_WORKFLOW.md) needs for a
pair in one dict: the W1, D1 and H1 grids (the trader's from the last
``chart.json`` capture of each timeframe, live or not, else the proposal),
whether each drawn leg grid still matches a fresh proposal, line values for
the next closes, and the pair's active plans with their checks and live
ticket; ``exposure`` nets the account's open orders by currency. Research only: nothing here
stores, draws, or places anything.
"""

from __future__ import annotations

from typing import Any

from app import mt4_bridge, mtf_channel as mc, trade_plan, trader_outbox
from app.trader_store import ORDER_TYPES, TraderStore

# Bars ahead for ``projections``: the next closes a plan is set against.
STEPS = {"W1": (1, 2), "D1": (1, 2, 3, 4, 5), "H1": (1, 12, 24, 48, 72)}
# A drawn grid "matches" its proposal when every line is within this share of
# the proposed width at the last bar (a re-anchor by hand moves more).
MATCH_TOL = 0.02
VISITS_SHOWN = 3


def digits(instrument: str) -> int:
    """MT4 price digits."""
    return 3 if "JPY" in instrument.upper() else 5


def _round_lines(values: dict[str, float], nd: int) -> dict[str, float]:
    return {k: round(v, nd) for k, v in values.items()}


def projections(bars: list[dict], grid: mc.Grid, steps: tuple[int, ...], nd: int) -> list[dict]:
    """The five lines at the close ``k`` bars after the last completed one
    (k=1 is the bar in progress). Weekend bars are skipped as MT4 does."""
    done = [b for b in bars if b.get("complete", True)]
    index = mc.BarIndex(done, grid.tf.bar_secs)
    last = len(done) - 1
    return [
        {"bars_ahead": k, "bar": mc._date(index.time(last + k), grid.tf),
         **{r: round(grid.lines[r].at(last + k), nd) for r in mc.ROLES}}
        for k in steps
    ]


def grid_status(
    drawn: mc.Grid | None, proposed: mc.Grid | None, at: float, nd: int
) -> dict[str, Any]:
    """``matches`` / ``differs`` / ``missing`` (no drawn grid) / ``no_proposal``."""
    if proposed is None:
        return {"status": "no_proposal" if drawn else "missing"}
    if drawn is None:
        return {"status": "missing"}
    width = mc.grid_width(proposed)
    diffs = {r: drawn.lines[r].at(at) - proposed.lines[r].at(at) for r in mc.ROLES}
    worst = max(abs(d) for d in diffs.values())
    out: dict[str, Any] = {
        "status": "matches" if worst <= MATCH_TOL * width else "differs",
        "drawn_width": round(mc.grid_width(drawn), nd),
        "proposed_width": round(width, nd),
    }
    if out["status"] == "differs":
        out["drawn_minus_proposed"] = _round_lines(diffs, nd)
    return out


def _slim_read(r: dict[str, Any]) -> dict[str, Any]:
    zone = r["zone"]["now"]
    visits = r.get("visits") or {}
    out = {k: r.get(k) for k in ("source", "warnings", "trend", "last_close", "zone",
                                 "zone_shares_pct", "past_outcomes_of_current_zone", "levels")}
    out["visits_count"] = {z: len(v) for z, v in visits.items()}
    out["visits_current_zone"] = (visits.get(zone) or [])[-VISITS_SHOWN:]
    rt = r.get("rail_tests") or {}
    out["rail_tests"] = {"rail": rt.get("rail"), "episodes": (rt.get("episodes") or [])[-5:]}
    return out


async def _w1(instrument: str, nd: int) -> dict[str, Any]:
    bars, grid = await mc.w1_grid(instrument)
    out = _slim_read(mc.read_grid(bars, grid, instrument=instrument))
    out["projections"] = projections(bars, grid, STEPS["W1"], nd)
    out["width"] = round(mc.grid_width(grid), nd)
    return out


async def _legs_tf(instrument: str, tfn: str, nd: int) -> dict[str, Any]:
    tf = mc.get_tf(tfn)
    bars = (await mc.load_bars(instrument, tf))["bars"]
    props = await mc.leg_proposals_for(instrument, tf, bars)
    objects = trader_outbox.load_chart(mt4_bridge.chart_dir(instrument, tfn)).get("objects") or []
    channels = {o.get("name") for o in objects if o.get("type") == "channel"}
    last = len([b for b in bars if b.get("complete", True)]) - 1
    out: dict[str, Any] = {
        "leg_reversal": props["leg_reversal"],
        "parent_grid": props.get(f"{mc.get_tf(tf.parent).name.lower()}_grid"),
        "legs": props["legs"],
    }
    for leg in ("previous", "current"):
        name = mc.leg_channel_name(instrument, props, leg)
        drawn = mc.grid_from_chart(bars, objects, name, tf) if name in channels else None
        try:
            proposed = mc.grid_from_leg(bars, props, leg)
        except mc.ChannelError as exc:
            proposed, note = None, str(exc)
        else:
            note = ""
        grid = drawn or proposed
        row: dict[str, Any] = {
            "leg": props[leg]["leg"],
            "channel_name": name,
            "grid": grid_status(drawn, proposed, last, nd),
        }
        if note:
            row["proposal_error"] = note
        if grid is None:
            out[leg] = row
            continue
        if drawn:
            drawn.source = f"chart: {name}"
        row.update(_slim_read(mc.read_grid(bars, grid, instrument=instrument)))
        row["width"] = round(mc.grid_width(grid), nd)
        row["projections"] = projections(bars, grid, STEPS[tfn], nd)
        out[leg] = row
    return out


async def _plans(instrument: str, store: TraderStore, now: int) -> list[dict[str, Any]]:
    symbol = mt4_bridge.map_symbol(instrument)
    out = []
    for row in store.list_plans(symbol):
        if row.get("status") != "active":
            continue
        order = row["order"]
        ticket_row = store.get_order(int(row["ticket"])) if row.get("ticket") else None
        live = await trade_plan.ticket(row)
        expiry = order.get("expiry")
        out.append({
            "id": row["id"],
            "title": (row.get("body_md") or "").splitlines()[0].lstrip("# "),
            "supersedes": row.get("supersedes"),
            "order": order,
            "ticket": row.get("ticket"),
            "ticket_state": None if ticket_row is None else {
                k: ticket_row.get(k) for k in ("type", "status", "open_price", "sl", "tp", "lots")},
            "expired": bool(expiry and expiry < now),
            "checks": await trade_plan.run_checks(instrument, order),
            "price": live.get("price"),
            "sizing": live.get("sizing"),
        })
    return out


def exposure(orders: list[dict[str, Any]]) -> dict[str, Any]:
    """Open tickets as lots long (+) / short (-) per currency, filled and
    pending apart. EURUSD buy 0.29 = EUR +0.29, USD -0.29."""
    out: dict[str, dict[str, float]] = {"filled": {}, "pending": {}}
    for o in orders:
        sym = str(o.get("symbol") or "").replace("_", "").upper()
        if len(sym) != 6 or o.get("status") != "open":
            continue
        otype = str(o.get("type") or "")
        sign = 1 if otype.startswith("buy") else -1
        book = out["pending" if otype in ORDER_TYPES else "filled"]
        lots = float(o.get("lots") or 0)
        for ccy, s in ((sym[:3], sign), (sym[3:], -sign)):
            book[ccy] = round(book.get(ccy, 0.0) + s * lots, 2)
    return out


def active_instruments(store: TraderStore) -> list[str]:
    """OANDA names of the pairs with an active plan."""
    syms = {r["symbol"] for r in store.list_plans() if r.get("status") == "active"}
    return [trade_plan.to_instrument(s) for s in sorted(syms)]


async def entry_read(instrument: str, store: TraderStore | None = None) -> dict[str, Any]:
    """The whole read for one pair. Errors come back per section."""
    store = store or TraderStore()
    nd = digits(instrument)
    out: dict[str, Any] = {
        "instrument": instrument,
        "symbol": mt4_bridge.map_symbol(instrument),
        "broker_now": mc._date(mc.broker_now(), mc.H1),
        "charts_live": {tf: mt4_bridge.check_chart(instrument, tf)[0] for tf in ("W1", "D1", "H1")},
    }
    sections = (("W1", _w1(instrument, nd)), ("D1", _legs_tf(instrument, "D1", nd)),
                ("H1", _legs_tf(instrument, "H1", nd)))
    for key, coro in sections:
        try:
            out[key] = await coro
        except (mc.ChannelError, mt4_bridge.Mt4BridgeError, OSError, ValueError) as exc:
            out[key] = {"error": str(exc)}
    try:
        out["price"] = await trade_plan.quote(instrument)
    except Exception as exc:  # noqa: BLE001 - the read stands without a live price
        out["price"] = {"error": str(exc)}
    out["plans"] = await _plans(instrument, store, mc.broker_now())
    return out
