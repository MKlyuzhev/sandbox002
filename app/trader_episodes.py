"""Bind chart objects to tickets and assemble trader episodes."""

from __future__ import annotations

import json
from typing import Any

from app.trader_outbox import parse_role_tags
from app.trader_store import TraderStore

SETUP_WINDOW_SEC = 4 * 3600
PRICE_EPS_PIPS = 2.0


def pip_size(symbol: str) -> float:
    name = (symbol or "").upper().replace("_", "")
    if name.endswith("JPY"):
        return 0.01
    return 0.0001


def _near(a: float | None, b: float | None, symbol: str) -> bool:
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= PRICE_EPS_PIPS * pip_size(symbol)


def _side(order_type: str) -> str:
    t = (order_type or "").lower()
    if t.startswith("buy"):
        return "long"
    if t.startswith("sell"):
        return "short"
    return "none"


def bind_objects(
    order: dict[str, Any],
    objects: list[dict[str, Any]],
    *,
    events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Link marks to a ticket. Priority: name in comment, SL/TP match, time window."""
    symbol = str(order.get("symbol") or "")
    comment = str(order.get("comment") or "")
    open_time = int(order.get("open_time") or 0)
    sl = order.get("sl")
    tp = order.get("tp")
    entry = order.get("open_price")
    links: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(obj: dict[str, Any], reason: str) -> None:
        name = str(obj.get("name") or "")
        if not name or name in seen:
            return
        seen.add(name)
        role = obj.get("role") or parse_role_tags(
            str(obj.get("text") or ""), str(obj.get("tooltip") or "")
        )["role"]
        links.append(
            {
                "object_name": name,
                "role": role,
                "reason": reason,
                "object": obj,
            }
        )

    for obj in objects:
        name = str(obj.get("name") or "")
        if name and name in comment:
            add(obj, "comment")

    for obj in objects:
        p1 = obj.get("p1")
        role = str(obj.get("role") or "unknown")
        if _near(p1, sl, symbol) and sl:
            add(obj, "sl_price")
        elif _near(p1, tp, symbol) and tp:
            add(obj, "tp_price")
        elif role == "entry" and _near(p1, entry, symbol):
            add(obj, "entry_mark")

    event_names = set()
    for ev in events or []:
        ts = int(ev.get("ts") or 0)
        kind = str(ev.get("kind") or "")
        if kind.startswith("object_") and open_time - SETUP_WINDOW_SEC <= ts <= open_time:
            if ev.get("name"):
                event_names.add(str(ev["name"]))
    for obj in objects:
        if str(obj.get("name") or "") in event_names:
            add(obj, "time_window")

    return links


def episode_id(symbol: str, ticket: int) -> str:
    return f"{symbol}:{ticket}"


def freeze_objects(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not snapshot:
        return []
    raw = snapshot.get("objects_json") or snapshot.get("objects") or "[]"
    if isinstance(raw, list):
        return list(raw)
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    return list(data) if isinstance(data, list) else []


def assemble_episode(
    order: dict[str, Any],
    timeframe: str,
    snapshot: dict[str, Any] | None,
    links: list[dict[str, Any]],
) -> dict[str, Any]:
    frozen = freeze_objects(snapshot)
    if not frozen:
        frozen = [link["object"] for link in links if link.get("object")]
    ticket = int(order["ticket"])
    symbol = str(order.get("symbol") or "")
    eid = episode_id(symbol, ticket)
    brief = (
        f"{symbol} {timeframe} ticket {ticket} {_side(str(order.get('type')))} "
        f"@ {order.get('open_price')} SL {order.get('sl')} TP {order.get('tp')}"
    )
    return {
        "id": eid,
        "ticket": ticket,
        "symbol": symbol,
        "timeframe": timeframe,
        "open_time": order.get("open_time"),
        "close_time": order.get("close_time"),
        "snapshot_id": snapshot.get("id") if snapshot else None,
        "objects_json": json.dumps(frozen, default=str),
        "brief": brief,
        "links": links,
        "side": _side(str(order.get("type"))),
    }


def sync_episodes(store: TraderStore, symbol: str, timeframe: str) -> list[str]:
    """Create/update episodes for orders on this chart."""
    snap = store.latest_snapshot(symbol, timeframe)
    objects = freeze_objects(snap)
    created: list[str] = []
    for order in store.list_orders(symbol=symbol, include_closed=True):
        at = int(order.get("open_time") or 0)
        frozen_snap = store.latest_snapshot(symbol, timeframe, at_ts=at) or snap
        frozen_objs = freeze_objects(frozen_snap) or objects
        links = bind_objects(order, frozen_objs)
        store.replace_links(int(order["ticket"]), links)
        ep = assemble_episode(order, timeframe, frozen_snap, links)
        store.upsert_episode(ep)
        created.append(ep["id"])
    return created
