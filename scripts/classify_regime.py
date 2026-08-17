#!/usr/bin/env python3
"""Classify Lien Ch.7 market regime from OANDA candles (no MCP).

Prints checklist JSON. Pass ``--mt4`` to overlay double Bollinger / SMA stack
on a matching SandboxChartBridge chart. See docs/LIEN_FX_STRATEGIES.md.

Examples:
    .venv/bin/python scripts/classify_regime.py
    .venv/bin/python scripts/classify_regime.py --instrument GBP_USD --granularity D
    .venv/bin/python scripts/classify_regime.py --granularity H1 --mt4
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import indicators, oanda_client, regime  # noqa: E402


async def run(args: argparse.Namespace) -> int:
    count: int | None = args.count
    if args.from_time and args.to_time:
        count = None

    try:
        payload = await oanda_client.get_candles(
            args.instrument,
            granularity=args.granularity,
            count=count,
            price="M",
            from_time=args.from_time,
            to_time=args.to_time,
        )
    except oanda_client.OandaError as exc:
        print(f"OANDA error: {exc}", file=sys.stderr)
        return 2

    bars = oanda_client.candles_to_bars(payload, prefer="mid")
    try:
        analysis = regime.analyze_bars(bars)
    except indicators.IndicatorError as exc:
        print(f"Indicator error: {exc}", file=sys.stderr)
        return 1

    analysis["instrument"] = args.instrument
    analysis["granularity"] = args.granularity
    if args.from_time:
        analysis["from_time"] = args.from_time
    if args.to_time:
        analysis["to_time"] = args.to_time
    if count is not None:
        analysis["count"] = count

    if args.mt4:
        from app import mt4_bridge  # noqa: E402

        result = mt4_bridge.apply_regime(
            analysis,
            bars,
            args.instrument,
            args.granularity,
            prefix=args.mt4_prefix,
        )
        analysis["mt4"] = {k: v for k, v in result.items() if k != "analysis"}
        if not result.get("ok"):
            print(f"MT4 error: {result.get('error')}", file=sys.stderr)
            print(json.dumps(analysis, indent=2, default=str))
            return 1

    print(json.dumps(analysis, indent=2, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lien Ch.7 regime classification (ADX / double BB / MA stack)."
    )
    parser.add_argument("--instrument", default="EUR_USD")
    parser.add_argument("--granularity", default="D")
    parser.add_argument(
        "--count",
        type=int,
        default=250,
        help="Candle count (paged above 5000). Ignored when both --from and --to are set.",
    )
    parser.add_argument(
        "--from",
        dest="from_time",
        default=None,
        metavar="RFC3339",
        help="Start time. With --count: N bars from this time.",
    )
    parser.add_argument(
        "--to",
        dest="to_time",
        default=None,
        metavar="RFC3339",
        help="End time. With --count: N bars ending at this time.",
    )
    parser.add_argument(
        "--mt4",
        action="store_true",
        help="Draw overlay on the MT4 chart via SandboxChartBridge (file inbox).",
    )
    parser.add_argument(
        "--mt4-prefix",
        default="sbox.regime.",
        help="Object name prefix for --mt4 (default: sbox.regime.).",
    )
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
