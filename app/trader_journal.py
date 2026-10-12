"""Trader journal actions shared by MCP and the dashboard.

Link the objects selected on an MT4 chart to a ticket, read a full record,
store analyses, and replay a record on the live chart. Research only: nothing
here places, modifies, or closes orders.
"""

from __future__ import annotations

from typing import Any

from app import mt4_bridge, trader_episodes, trader_outbox
from app.trader_store import TraderStore


class TraderJournalError(ValueError):
    pass


def selected_objects(instrument: str, timeframe: str) -> list[dict[str, Any]]:
    """User objects currently selected on the live chart (EA v1.07+)."""
    ok, reason = mt4_bridge.check_chart(instrument, timeframe)
    if not ok:
        raise TraderJournalError(reason)
    chart = trader_outbox.load_chart(mt4_bridge.chart_dir(instrument, timeframe))
    objects = list(chart.get("objects") or [])
    if objects and "selected" not in objects[0]:
        raise TraderJournalError(
            "chart.json has no 'selected' flag; recompile SandboxChartBridge v1.07+."
        )
    return [o for o in objects if o.get("selected")]


def _pick_ticket(store: TraderStore, symbol: str, ticket: int | None) -> int:
    if ticket:
        if store.get_order(int(ticket)) is None:
            raise TraderJournalError(f"ticket {ticket} is not in the journal; run trader_sync.")
        return int(ticket)
    open_orders = [
        o for o in store.list_orders(symbol=symbol, include_closed=False)
    ]
    if len(open_orders) == 1:
        return int(open_orders[0]["ticket"])
    recent = [int(o["ticket"]) for o in store.list_orders(symbol=symbol)[:5]]
    if not open_orders:
        raise TraderJournalError(f"no open {symbol} order; pass ticket (recent: {recent}).")
    raise TraderJournalError(
        f"several open {symbol} orders; pass ticket (open: "
        f"{[int(o['ticket']) for o in open_orders]})."
    )


def link_selected(
    store: TraderStore, instrument: str, timeframe: str, ticket: int | None = None
) -> dict[str, Any]:
    """Freeze the objects selected in MT4 as the ticket's linked marks."""
    objects = selected_objects(instrument, timeframe)
    if not objects:
        raise TraderJournalError("select objects in MT4 first (click, Ctrl+click for more).")
    symbol = mt4_bridge.map_symbol(instrument)
    target = _pick_ticket(store, symbol, ticket)
    for obj in objects:
        obj.pop("selected", None)
    store.set_manual_links(target, objects)
    # The chart you link on owns the episode: its timeframe drives the review.
    trader_episodes.sync_episodes(
        store, symbol, mt4_bridge.map_timeframe(timeframe)[0], owned_tickets={target}
    )
    return {
        "ok": True,
        "ticket": target,
        "linked": [o.get("name") for o in objects],
        "note": "review cleared; the next trader_sync re-scores against these marks",
    }


def record(store: TraderStore, ticket: int) -> dict[str, Any]:
    rec = store.get_record(int(ticket))
    if rec is None:
        raise TraderJournalError(f"ticket {ticket} is not in the journal; run trader_sync.")
    return rec


def replay(
    store: TraderStore,
    ticket: int,
    *,
    timeframe: str = "",
    with_analyses: bool = True,
) -> dict[str, Any]:
    rec = record(store, ticket)
    linked = [link["object"] for link in rec["links"] if link.get("object")]
    levels = (
        [a["levels"] for a in rec["analyses"] if a.get("levels")] if with_analyses else []
    )
    result = mt4_bridge.apply_replay(
        rec["order"],
        linked,
        revisions=rec["revisions"],
        levels=levels,
        timeframe=timeframe or None,
    )
    result["linked"] = len(linked)
    result["analysis_levels"] = sum(len(g) for g in levels)
    return result


def clear_replay(symbol: str, ticket: int | None = None, timeframe: str = "") -> dict[str, Any]:
    try:
        chart = mt4_bridge.live_chart_for(symbol, timeframe or None)
    except mt4_bridge.Mt4BridgeError as exc:
        return {"ok": False, "error": str(exc)}
    prefix = mt4_bridge.REPLAY_PREFIX + (f"{int(ticket)}." if ticket else "")
    result = mt4_bridge.clear_layer(
        prefix, symbol=symbol, timeframe=str(chart["timeframe"])
    )
    result.pop("status", None)
    return result


def add_analysis(
    store: TraderStore,
    *,
    instrument: str,
    kind: str,
    body_md: str,
    timeframe: str = "",
    ticket: int | None = None,
    author: str = "claude",
    model: str = "",
    refs: list[str] | None = None,
    levels: list[dict[str, Any]] | None = None,
    drawn_prefix: str = "",
    order: dict[str, Any] | None = None,
    supersedes: int | None = None,
) -> dict[str, Any]:
    """Store an analysis. With ``order`` it is a plan: an order spec the
    trader enters in MT4 by hand (``app.trade_plan``); nothing sends it."""
    from app import trade_plan

    symbol = mt4_bridge.map_symbol(instrument)
    if order is not None:
        if kind != "pre_trade":
            raise TraderJournalError("only a pre_trade analysis can carry an order")
        try:
            order = trade_plan.validate_order(order)
        except (trade_plan.PlanError, ValueError) as exc:
            raise TraderJournalError(str(exc)) from exc
    if supersedes:
        old = store.get_analysis(int(supersedes))
        if old is None or old["symbol"] != symbol:
            raise TraderJournalError(f"analysis {supersedes} is not a {symbol} analysis")
    if ticket and store.get_order(int(ticket)) is None:
        raise TraderJournalError(f"ticket {ticket} is not in the journal; run trader_sync.")
    for obj in levels or []:
        # Levels must be drawable later; validate against the bridge schema now.
        mt4_bridge._normalize_object(obj, "")
    try:
        return store.add_analysis(
            symbol=symbol,
            timeframe=mt4_bridge.map_timeframe(timeframe)[0] if timeframe else "",
            kind=kind,
            body_md=body_md,
            author=author,
            ticket=int(ticket) if ticket else None,
            model=model,
            refs=refs,
            levels=levels,
            drawn_prefix=drawn_prefix,
            order=order,
            supersedes=int(supersedes) if supersedes else None,
        )
    except ValueError as exc:
        raise TraderJournalError(str(exc)) from exc


def plan(store: TraderStore, analysis_id: int) -> dict[str, Any]:
    row = store.get_analysis(int(analysis_id))
    if row is None or not row.get("order"):
        raise TraderJournalError(f"analysis {analysis_id} is not a plan")
    return row


def set_plan_status(store: TraderStore, analysis_id: int, status: str) -> dict[str, Any]:
    plan(store, analysis_id)
    try:
        store.set_plan_status(int(analysis_id), status)
    except ValueError as exc:
        raise TraderJournalError(str(exc)) from exc
    return plan(store, analysis_id)


def link_plan(store: TraderStore, analysis_id: int, ticket: int) -> dict[str, Any]:
    row = plan(store, analysis_id)
    order = store.get_order(int(ticket))
    if order is None:
        raise TraderJournalError(f"ticket {ticket} is not in the journal; run trader_sync.")
    if mt4_bridge.map_symbol(str(order.get("symbol") or "")) != row["symbol"]:
        raise TraderJournalError(f"ticket {ticket} is {order.get('symbol')}, plan is {row['symbol']}")
    store.attach_analysis(int(analysis_id), int(ticket), "explicit")
    return plan(store, analysis_id)


def draw_plan(store: TraderStore, analysis_id: int) -> dict[str, Any]:
    """The plan's levels on the live chart for its symbol, under ``sbox.plan.<id>.``."""
    row = plan(store, analysis_id)
    if not row.get("levels"):
        raise TraderJournalError(f"plan {analysis_id} has no levels")
    try:
        chart = mt4_bridge.live_chart_for(row["symbol"])
    except mt4_bridge.Mt4BridgeError as exc:
        raise TraderJournalError(str(exc)) from exc
    result = mt4_bridge.upsert_objects(
        row["symbol"], str(chart["timeframe"]), row["levels"],
        prefix=f"sbox.plan.{int(analysis_id)}.", clear_prefix_first=True,
    )
    result.pop("status", None)
    result["timeframe"] = chart["timeframe"]
    return result


def trade_summaries(store: TraderStore, symbol: str | None = None) -> list[dict[str, Any]]:
    """One row per ticket for list views."""
    rows = []
    for order in store.list_orders(symbol=symbol):
        ticket = int(order["ticket"])
        rec = store.get_record(ticket) or {}
        review = rec.get("review") or {}
        rows.append(
            {
                "ticket": ticket,
                "symbol": order.get("symbol"),
                "side": "short" if str(order.get("type")).startswith("sell") else "long",
                "lots": order.get("lots"),
                "open_price": order.get("open_price"),
                "open_time": order.get("open_time"),
                "close_price": order.get("close_price"),
                "close_time": order.get("close_time"),
                "profit": order.get("profit"),
                "status": order.get("status"),
                "timeframe": (rec.get("episode") or {}).get("timeframe"),
                "linked": sum(1 for link in rec.get("links") or [] if link["source"] == "manual"),
                "analyses": len(rec.get("analyses") or []),
                "model_fit": review.get("model_fit"),
                "entry_quality": review.get("entry_quality"),
                "exit_quality": review.get("exit_quality"),
            }
        )
    return rows
