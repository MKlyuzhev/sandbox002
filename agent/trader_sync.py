"""Poll MT4 outbox folders into trader.sqlite and run post-trade reviews."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Callable

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app import mt4_bridge, trader_episodes, trader_outbox, trader_review
from app.trader_store import DEFAULT_DB_PATH, TraderStore

FetchBarsFn = Callable[[str, str, int, int], Any]


async def _default_fetch_bars(
    instrument: str, granularity: str, from_ts: int, to_ts: int
) -> list[dict[str, Any]]:
    from datetime import datetime, timezone

    from app import oanda_client

    def _rfc(ts: int) -> str:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000000000Z"
        )

    gran = trader_outbox.oanda_granularity(granularity)
    now = int(datetime.now(timezone.utc).timestamp())
    payload = await oanda_client.get_candles(
        instrument,
        granularity=gran,
        count=None,  # from+to window; count would make OANDA reject it
        from_time=_rfc(max(0, from_ts - 3600)),
        to_time=_rfc(min(to_ts + 3600 * 24, now)),  # OANDA rejects future 'to'
        price="M",
    )
    return oanda_client.candles_to_bars(payload, prefer="mid")


ORDER_EVENT_KINDS = ("order_open", "order_modify", "order_close")


def _heartbeat(chart_dir: Path) -> dict[str, Any] | None:
    return mt4_bridge._load_heartbeat(chart_dir / mt4_bridge.HEARTBEAT_NAME)


def _heartbeat_mtime(chart_dir: Path) -> float:
    try:
        return (chart_dir / mt4_bridge.HEARTBEAT_NAME).stat().st_mtime
    except OSError:
        return 0.0


def _float_or_none(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def to_broker_time(bar: dict[str, Any], offset: int) -> dict[str, Any]:
    """Shift a UTC OANDA bar onto the broker clock used by orders and marks."""
    out = dict(bar)
    ts = trader_review._unix(bar.get("time"))
    out["time"] = ts + offset if ts else bar.get("time")
    return out


def sync_chart_dir(
    store: TraderStore, chart_dir: Path, *, live: bool | None = None
) -> dict[str, Any]:
    """Upsert one chart folder. ``live`` (fresh heartbeat) gates SL/TP polling:
    a stale folder's orders.json must not add revisions."""
    chart = trader_outbox.load_chart(chart_dir)
    symbol = str(chart.get("symbol") or chart_dir.name.split("_")[0])
    timeframe = str(chart.get("timeframe") or "")
    if "_" in chart_dir.name:
        # GBPUSD_H1
        parts = chart_dir.name.rsplit("_", 1)
        if not chart.get("symbol"):
            symbol = parts[0]
        if not timeframe:
            timeframe = parts[1]
    ts = int(chart.get("time_current") or 0)
    objects = list(chart.get("objects") or [])
    snap_id = store.upsert_snapshot(symbol, timeframe, ts, objects)
    events = trader_outbox.load_events(chart_dir / "events.jsonl")
    store.append_events(symbol, timeframe, events)
    hb = _heartbeat(chart_dir)
    offset = mt4_bridge.offset_from_heartbeat(hb) if hb else None
    if live is None:
        age = mt4_bridge.heartbeat_age_sec(chart_dir / mt4_bridge.HEARTBEAT_NAME)
        live = age is not None and age <= mt4_bridge.HEARTBEAT_STALE_SEC

    order_events = sorted(
        (e for e in events if e.get("kind") in ORDER_EVENT_KINDS and e.get("ticket")),
        key=lambda e: (int(e.get("ts") or 0), int(e.get("seq") or 0)),
    )
    opened_here = {int(e["ticket"]) for e in order_events if e["kind"] == "order_open"}

    live_orders = trader_outbox.load_orders(chart_dir, history=False)
    hist = trader_outbox.load_orders(chart_dir, history=True)
    new_tickets: list[int] = []
    for order, status in [(o, "open") for o in live_orders.get("orders") or []] + [
        (o, "closed") for o in hist.get("orders") or []
    ]:
        if store.upsert_order(order, status=status, utc_offset=offset):
            new_tickets.append(int(order["ticket"]))
        if live and status == "open":
            store.add_revision(
                int(order["ticket"]),
                ts or int(order.get("open_time") or 0),
                _float_or_none(order.get("sl")),
                _float_or_none(order.get("tp")),
                "poll",
            )

    for ev in order_events:
        ticket = int(ev["ticket"])
        if store.get_order(ticket) is None:
            continue
        sl, tp = _float_or_none(ev.get("sl")), _float_or_none(ev.get("tp"))
        if ev["kind"] == "order_open":
            store.set_open_levels(ticket, sl, tp)
        store.add_revision(ticket, int(ev.get("ts") or 0), sl, tp, "event")

    # Deleted or expired pending orders never reach history.json (the EA skips
    # types above OP_SELL). The EA's order_close event says one left; a live
    # folder's orders.json (complete for its symbol) says it is gone.
    listed = {int(o["ticket"]) for o in live_orders.get("orders") or []}
    gone = {int(e["ticket"]) for e in order_events if e["kind"] == "order_close"}
    if live and "orders" in live_orders:
        gone |= {int(o["ticket"]) for o in store.list_orders(symbol=symbol, include_closed=False)}
    deleted = store.mark_deleted(sorted(gone - listed))

    for ticket in new_tickets:
        order = store.get_order(ticket) or {}
        open_utc = int(order.get("open_time") or 0) - int(order.get("utc_offset") or 0)
        store.attach_recent_analyses(ticket, symbol, open_utc)

    ids = trader_episodes.sync_episodes(
        store, symbol, timeframe, owned_tickets=opened_here
    )
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "snapshot_id": snap_id,
        "episodes": ids,
        "objects": len(objects),
        "live": live,
        "new_tickets": new_tickets,
        "deleted": deleted,
    }


async def review_pending(
    store: TraderStore,
    *,
    fetch_bars: FetchBarsFn | None = None,
    bars_by_episode: dict[str, list[dict[str, Any]]] | None = None,
) -> list[str]:
    done: list[str] = []
    fetcher = fetch_bars or _default_fetch_bars
    for ep in store.list_episodes(has_fill=True):
        if store.get_review(ep["id"]):
            continue
        order = store.get_order(int(ep["ticket"]))
        if not order:
            continue
        objects = trader_episodes.freeze_objects(ep)
        # Orders and marks are in broker time; OANDA bars are UTC.
        offset = int(order.get("utc_offset") or 0)
        bars = (bars_by_episode or {}).get(ep["id"])
        fetch_error: str | None = None
        if bars is None:
            instrument = trader_outbox.oanda_instrument(str(ep["symbol"]))
            open_ts = int(ep.get("open_time") or 0) - offset
            close_ts = (int(ep.get("close_time") or 0) - offset) if ep.get("close_time") else open_ts
            try:
                bars = await fetcher(
                    instrument,
                    str(ep["timeframe"]),
                    open_ts,
                    close_ts + 3600 * 24,
                )
            except Exception as exc:  # score without bars, but say why
                bars = []
                fetch_error = f"{type(exc).__name__}: {exc}"
            bars = [to_broker_time(b, offset) for b in bars]
        review = trader_review.review_trade(order=order, objects=objects, bars=bars)
        if fetch_error:
            review["diagnostics"]["bars_error"] = fetch_error
        review["episode_id"] = ep["id"]
        store.upsert_review(review)
        brief = f"{ep.get('brief') or ''} | {review['brief']}"
        ep["brief"] = brief
        store.upsert_episode(ep)
        done.append(ep["id"])
    return done


async def sync_all(
    store: TraderStore,
    files_dir: Path | None = None,
    *,
    fetch_bars: FetchBarsFn | None = None,
    bars_by_episode: dict[str, list[dict[str, Any]]] | None = None,
    review: bool = True,
) -> dict[str, Any]:
    charts = []
    # Oldest outbox first: every chart folder carries the account's orders,
    # and the most recently written one must win.
    for path in sorted(trader_outbox.iter_chart_dirs(files_dir), key=_heartbeat_mtime):
        charts.append(sync_chart_dir(store, path))
    reviewed: list[str] = []
    if review:
        reviewed = await review_pending(
            store, fetch_bars=fetch_bars, bars_by_episode=bars_by_episode
        )
    return {"charts": charts, "reviewed": reviewed}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync MT4 trader outbox into sqlite and score closed trades."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--files-dir", type=Path, default=None)
    parser.add_argument("--no-review", action="store_true")
    args = parser.parse_args()
    store = TraderStore(args.db)
    result = asyncio.run(
        sync_all(store, args.files_dir, review=not args.no_review)
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
