"""Causal regime-change campaign: USD_JPY daily, 2015-now, rest fill.

Research only; no orders. Stage-3 paper tickets fill at the next bar's
taking-side open (long ask / short bid). vppu=1/150 (JPY→USD at 150).
Detection-quality metrics sit beside the equity curve.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent
_S002 = OUT.parents[1]
if str(_S002) not in sys.path:
    sys.path.insert(0, str(_S002))

from agent.schema import PaperTrade  # noqa: E402
from agent.walk_jobs import execute_walk  # noqa: E402
from app.walk_fetch import fetch_walk_bars  # noqa: E402

FROM_T = "2015-01-01T00:00:00Z"
TO_T = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
INSTRUMENT = "USD_JPY"
LOOKBACK = 250
START_EQUITY = 10_000.0
RISK_FRAC = 0.02
VPPU = 1.0 / 150.0

D_CACHE = OUT / "usd_jpy_d_mba.json"
TRADES_PATH = OUT / "regime_change_trades.json"
SUMMARY_PATH = OUT / "regime_change_campaign.json"


async def load_mba() -> list[dict]:
    if D_CACHE.exists():
        bars = json.loads(D_CACHE.read_text())
        print(f"CACHE {D_CACHE.name} n={len(bars)} last={bars[-1]['time']}", flush=True)
        return bars
    print(f"FETCH {INSTRUMENT} D MBA {FROM_T} .. {TO_T}", flush=True)
    bars = await fetch_walk_bars(
        INSTRUMENT, "D", FROM_T, TO_T, LOOKBACK, with_ba=True
    )
    D_CACHE.write_text(json.dumps(bars))
    print(f"WROTE {D_CACHE.name} n={len(bars)} last={bars[-1]['time']}", flush=True)
    return bars


async def cached_fetch(
    instrument: str,
    granularity: str,
    from_time: str,
    to_time: str,
    lookback: int,
    *,
    with_ba: bool = False,
) -> list[dict]:
    del instrument, granularity, from_time, to_time, lookback, with_ba
    return await load_mba()


def year_of(ts: str) -> str:
    return (ts or "")[:4]


def year_rows(trades: list[PaperTrade], starting: float) -> list[dict]:
    by: dict[str, list[PaperTrade]] = defaultdict(list)
    for t in trades:
        by[year_of(t.entry_time)].append(t)
    rows = []
    eq = starting
    for y in sorted(by):
        chunk = by[y]
        rs = [float(t.r_realized) for t in chunk if t.r_realized is not None]
        wins = sum(1 for r in rs if r > 0)
        start_eq = eq
        if chunk and chunk[-1].equity_after is not None:
            eq = float(chunk[-1].equity_after)
        sides = Counter(t.side for t in chunk)
        exits = Counter(t.exit_status for t in chunk)
        rows.append(
            {
                "year": y,
                "n": len(chunk),
                "wins": wins,
                "win_rate": round(wins / len(rs), 4) if rs else None,
                "mean_r": round(sum(rs) / len(rs), 4) if rs else None,
                "sum_r": round(sum(rs), 4) if rs else None,
                "starting_equity": round(start_eq, 2),
                "ending_equity": round(eq, 2),
                "long": sides.get("long", 0),
                "short": sides.get("short", 0),
                "exits": dict(exits),
            }
        )
    return rows


def side_block(trades: list[PaperTrade], side: str) -> dict:
    chunk = [t for t in trades if t.side == side]
    rs = [float(t.r_realized) for t in chunk if t.r_realized is not None]
    n = len(rs)
    return {
        "n": len(chunk),
        "mean_r": round(sum(rs) / n, 4) if n else None,
        "win_rate": round(sum(1 for r in rs if r > 0) / n, 4) if n else None,
        "sum_r": round(sum(rs), 4) if n else None,
    }


def compact_detection(detection: dict) -> dict:
    episodes = list(detection.get("episodes") or [])
    by_state: dict[str, int] = Counter(str(e.get("state")) for e in episodes)
    followed = [e for e in episodes if e.get("followed_by_change")]
    return {
        "horizon": detection.get("horizon"),
        "step_count": detection.get("step_count"),
        "state_counts": detection.get("state_counts"),
        "detections": detection.get("detections"),
        "true_positives": detection.get("true_positives"),
        "precision": detection.get("precision"),
        "recall": detection.get("recall"),
        "flip_count": detection.get("flip_count"),
        "mean_lead_time": detection.get("mean_lead_time"),
        "episodes_by_first_state": dict(by_state),
        "episodes_followed": len(followed),
        "episodes": episodes,
    }


async def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    bars = await load_mba()
    print(
        f"WALK regime_change rest USD_JPY n={len(bars)} "
        f"{bars[0]['time']} .. {bars[-1]['time']}",
        flush=True,
    )
    result, meta = await execute_walk(
        "regime_change",
        INSTRUMENT,
        FROM_T,
        TO_T,
        granularity="D",
        lookback=LOOKBACK,
        fill_mode="rest",
        balance=START_EQUITY,
        risk_fraction=RISK_FRAC,
        value_per_price_unit=VPPU,
        no_journal=True,
        fetch_fn=cached_fetch,
    )
    trades = result.trades
    equity = result.equity
    detection = compact_detection(meta.get("detection") or {})
    dump = [t.model_dump(mode="json") for t in trades]
    TRADES_PATH.write_text(json.dumps(dump, indent=2) + "\n")

    summary = {
        "instrument": INSTRUMENT,
        "engine": "regime_change",
        "kind": "regime_change",
        "fill_mode": "rest",
        "note": (
            "Causal structural regime-change walk. Stage-3 confirmed first-fire "
            "only; next-bar rest fill (long ask / short bid). Tick volume is a "
            "proxy. Research only; no orders."
        ),
        "window": f"{FROM_T} to {TO_T}",
        "last_daily": bars[-1]["time"] if bars else None,
        "value_per_price_unit": VPPU,
        "value_per_price_unit_note": (
            "USD per 1.0 USDJPY move per 1 unit; JPY→USD at 150. "
            "Scales cash P&L and size, not R."
        ),
        "starting_equity": START_EQUITY,
        "risk_fraction": RISK_FRAC,
        "lookback": LOOKBACK,
        "bar_count": meta.get("bar_count"),
        "start_index": meta.get("start_index"),
        "walk_id": result.walk_id,
        "equity": equity.model_dump(mode="json"),
        "trade_count": len(trades),
        "exits": dict(Counter(t.exit_status for t in trades)),
        "by_side": {
            "long": side_block(trades, "long"),
            "short": side_block(trades, "short"),
        },
        "years": year_rows(trades, START_EQUITY),
        "detection": detection,
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, default=str) + "\n")
    print(
        json.dumps(
            {
                k: summary[k]
                for k in (
                    "instrument",
                    "window",
                    "last_daily",
                    "trade_count",
                    "equity",
                    "exits",
                    "by_side",
                    "years",
                )
            }
            | {
                "detection": {
                    k: detection[k]
                    for k in (
                        "step_count",
                        "state_counts",
                        "detections",
                        "true_positives",
                        "precision",
                        "recall",
                        "flip_count",
                        "mean_lead_time",
                    )
                    if k in detection
                }
            },
            indent=2,
            default=str,
        ),
        flush=True,
    )
    print(f"WROTE {SUMMARY_PATH}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
