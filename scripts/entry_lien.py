#!/usr/bin/env python3
"""Lien Ch.13 / 14 / 16 entry signal from OANDA candles (no MCP).

Chapter 13 Fader (D + H1), 14 20-day breakout (D), 16 perfect order (D).
Prints a signal JSON. Research only; no orders. See docs/LIEN_FX_STRATEGIES.md.

Examples:
    .venv/bin/python scripts/entry_lien.py --chapter 16 --instrument USD_JPY
    .venv/bin/python scripts/entry_lien.py --chapter 14 --instrument GBP_USD
    .venv/bin/python scripts/entry_lien.py --chapter 13 --instrument EUR_USD
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.engines import breakout20, fader, perfect_order  # noqa: E402
from agent.lien_chapters import ENTRY_LIEN_CHAPTERS, entry_lien_error  # noqa: E402
from app import indicators, oanda_client, regime  # noqa: E402


async def _analyze(instrument: str, granularity: str, count: int) -> tuple[list, dict]:
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
    return bars, analysis


async def run(args: argparse.Namespace) -> int:
    if args.chapter not in ENTRY_LIEN_CHAPTERS:
        print(entry_lien_error(args.chapter), file=sys.stderr)
        return 2

    try:
        bars, analysis = await _analyze(
            args.instrument, args.granularity, args.count
        )
        if args.chapter == 13:
            _ltf_bars, ltf_analysis = await _analyze(
                args.instrument, args.ltf_granularity, args.ltf_count
            )
            result = fader.fader_signal(
                analysis,
                ltf_analysis,
                args.instrument,
                buffer_pips=args.buffer_pips,
                probe_pips=args.probe_pips,
                htf_granularity=args.granularity,
                ltf_granularity=args.ltf_granularity,
            )
        elif args.chapter == 14:
            result = breakout20.breakout20_signal(
                analysis,
                args.instrument,
                buffer_pips=args.buffer_pips,
                granularity=args.granularity,
                bars=bars,
            )
        else:
            result = perfect_order.perfect_order_signal(
                analysis,
                args.instrument,
                buffer_pips=args.buffer_pips,
                granularity=args.granularity,
                bars=bars,
            )
    except oanda_client.OandaError as exc:
        print(f"OANDA error: {exc}", file=sys.stderr)
        return 2
    except indicators.IndicatorError as exc:
        print(f"Indicator error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lien Ch.13/14/16 entry signal (fader, 20-day breakout, perfect order)."
    )
    parser.add_argument(
        "--chapter",
        type=int,
        required=True,
        help="13 (fader), 14 (20-day breakout), or 16 (perfect order).",
    )
    parser.add_argument("--instrument", default="EUR_USD")
    parser.add_argument("--granularity", default="D", help="Primary/HTF (default D).")
    parser.add_argument(
        "--ltf-granularity",
        default="H1",
        help="Lower TF for Ch.13 (default H1).",
    )
    parser.add_argument("--count", type=int, default=250)
    parser.add_argument("--ltf-count", type=int, default=250)
    parser.add_argument("--buffer-pips", type=int, default=10)
    parser.add_argument(
        "--probe-pips",
        type=int,
        default=15,
        help="Ch.13 probe beyond prior day H/L (default 15).",
    )
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
