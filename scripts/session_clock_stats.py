#!/usr/bin/env python3
"""Hour-of-week range stats + Ch.11 hunt/reverse rates from OHLCV (no PnL fit).

Joins OANDA bar timestamps to a calendar clock. Research only; no orders.

Examples:
    .venv/bin/python scripts/session_clock_stats.py --instrument GBP_USD
    .venv/bin/python scripts/session_clock_stats.py --clock utc_fixed --count 5000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.levels import pip_size  # noqa: E402
from app import oanda_client  # noqa: E402
from app.session_clock import (  # noqa: E402
    hour_of_week_ranges,
    waiting_deal_day_outcomes,
)


async def run(args: argparse.Namespace) -> int:
    try:
        payload = await oanda_client.get_candles(
            args.instrument,
            granularity=args.granularity,
            count=args.count,
            price="M",
        )
    except oanda_client.OandaError as exc:
        print(f"OANDA error: {exc}", file=sys.stderr)
        return 2
    bars = oanda_client.candles_to_bars(payload, prefer="mid")
    pip = pip_size(args.instrument)
    hour_rows = hour_of_week_ranges(bars, pip)
    by_mean = sorted(hour_rows, key=lambda r: r["mean_range_pips"], reverse=True)
    out = {
        "instrument": args.instrument,
        "granularity": args.granularity,
        "bar_count": len(bars),
        "clock": args.clock,
        "waiting_deal": waiting_deal_day_outcomes(
            bars, pip, hunt_pips=args.hunt_pips, clock=args.clock
        ),
        "hour_of_week_top": by_mean[:12],
        "hour_of_week": hour_rows,
        "note": (
            "Calendar correlation on OHLCV timestamps. Not a PnL retune. "
            "News/FOMC is not identified from range fatness. "
            "Scheduled FOMC skip-day is documented hygiene, not this script."
        ),
    }
    print(json.dumps(out, indent=2, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Session-clock stats from OANDA candles (research only)."
    )
    parser.add_argument("--instrument", default="GBP_USD")
    parser.add_argument("--granularity", default="M15")
    parser.add_argument("--count", type=int, default=5000)
    parser.add_argument("--hunt-pips", type=float, default=25.0)
    parser.add_argument(
        "--clock",
        choices=("frankfurt", "utc_fixed"),
        default="frankfurt",
        help="frankfurt = DST-aware Berlin 08:00 to London 08:00; utc_fixed = 06–07 UTC.",
    )
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
