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

from app import trader_episodes, trader_outbox, trader_review
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
    payload = await oanda_client.get_candles(
        instrument,
        granularity=gran,
        from_time=_rfc(max(0, from_ts - 3600)),
        to_time=_rfc(to_ts + 3600 * 24),
        price="M",
    )
    return oanda_client.candles_to_bars(payload, prefer="mid")


def sync_chart_dir(store: TraderStore, chart_dir: Path) -> dict[str, Any]:
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

    live = trader_outbox.load_orders(chart_dir, history=False)
    for order in live.get("orders") or []:
        store.upsert_order(order, status="open")
    hist = trader_outbox.load_orders(chart_dir, history=True)
    for order in hist.get("orders") or []:
        store.upsert_order(order, status="closed")

    ids = trader_episodes.sync_episodes(store, symbol, timeframe)
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "snapshot_id": snap_id,
        "episodes": ids,
        "objects": len(objects),
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
        bars = (bars_by_episode or {}).get(ep["id"])
        if bars is None:
            instrument = trader_outbox.oanda_instrument(str(ep["symbol"]))
            open_ts = int(ep.get("open_time") or 0)
            close_ts = int(ep.get("close_time") or open_ts)
            try:
                bars = await fetcher(
                    instrument,
                    str(ep["timeframe"]),
                    open_ts,
                    close_ts + 3600 * 24,
                )
            except Exception:
                bars = []
        review = trader_review.review_trade(order=order, objects=objects, bars=bars)
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
    for path in trader_outbox.iter_chart_dirs(files_dir):
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
