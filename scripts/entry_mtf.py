#!/usr/bin/env python3
"""Lien Ch.8 Multiple Time Frame entry signal from OANDA candles (no MCP).

Higher timeframe sets trend direction; the lower timeframe times the entry on
an RSI pullback (buy dips in uptrends, sell rallies in downtrends). Prints a
signal JSON. Research only; no orders. See docs/LIEN_FX_STRATEGIES.md.

Examples:
    .venv/bin/python scripts/entry_mtf.py --instrument USD_JPY
    .venv/bin/python scripts/entry_mtf.py --instrument GBP_USD \
        --htf-granularity D --ltf-granularity H1 --rsi-os 30 --rsi-ob 70
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.engines import mtf  # noqa: E402
from app import indicators, oanda_client, regime  # noqa: E402


async def _analyze(instrument: str, granularity: str, count: int) -> dict:
    payload = await oanda_client.get_candles(
        instrument,
        granularity=granularity,
        count=count,
        price="M",
    )
    bars = oanda_client.candles_to_bars(payload, prefer="mid")
    analysis = regime.analyze_bars(bars)
    analysis["instrument"] = instrument
    analysis["granularity"] = granularity
    return analysis


async def run(args: argparse.Namespace) -> int:
    try:
        htf_analysis = await _analyze(
            args.instrument, args.htf_granularity, args.htf_count
        )
        ltf_analysis = await _analyze(
            args.instrument, args.ltf_granularity, args.ltf_count
        )
    except oanda_client.OandaError as exc:
        print(f"OANDA error: {exc}", file=sys.stderr)
        return 2
    except indicators.IndicatorError as exc:
        print(f"Indicator error: {exc}", file=sys.stderr)
        return 1

    result = mtf.mtf_signal(
        htf_analysis,
        ltf_analysis,
        args.instrument,
        rsi_os=args.rsi_os,
        rsi_ob=args.rsi_ob,
        buffer_pips=args.buffer_pips,
        htf_granularity=args.htf_granularity,
        ltf_granularity=args.ltf_granularity,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lien Ch.8 Multiple Time Frame entry signal."
    )
    parser.add_argument("--instrument", default="EUR_USD")
    parser.add_argument(
        "--htf-granularity",
        default="D",
        help="Higher timeframe for trend direction (default: D).",
    )
    parser.add_argument(
        "--ltf-granularity",
        default="H1",
        help="Lower timeframe for entry timing (default: H1).",
    )
    parser.add_argument("--htf-count", type=int, default=250)
    parser.add_argument("--ltf-count", type=int, default=250)
    parser.add_argument(
        "--rsi-os",
        type=float,
        default=30.0,
        help="Oversold RSI dip threshold for longs (default: 30).",
    )
    parser.add_argument(
        "--rsi-ob",
        type=float,
        default=70.0,
        help="Overbought RSI rally threshold for shorts (default: 70).",
    )
    parser.add_argument(
        "--buffer-pips",
        type=int,
        default=10,
        help="Stop buffer off the lower-TF high/low (default: 10).",
    )
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
