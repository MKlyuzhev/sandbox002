#!/usr/bin/env python3
"""Detect a structural regime change from OANDA candles (no MCP).

Prints the staged ``regime_change`` block: state (stable | early_warning |
confirming | confirmed), direction_from/to, a capped weighted score, and the
cited evidence (trendlines, S/R levels + role reversal, channels, fan lines,
swing flips, OHLCV candlestick confirmations, tick-volume). Whole corpus is
the reference (Murphy, Edwards & Magee, Pring, Nison, Lien). Research only; no
orders. See docs/REGIME_CHANGE_FRAMEWORK.md.

Examples:
    .venv/bin/python scripts/detect_regime_change.py
    .venv/bin/python scripts/detect_regime_change.py --instrument GBP_USD --granularity D
    .venv/bin/python scripts/detect_regime_change.py --granularity H1 --evidence-only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import indicators, oanda_client, regime, regime_change  # noqa: E402


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
    block = regime_change.detect(bars, analysis, instrument=args.instrument)
    block["instrument"] = args.instrument
    block["granularity"] = args.granularity

    if args.evidence_only:
        block = {
            "instrument": args.instrument,
            "granularity": args.granularity,
            "state": block["state"],
            "direction_from": block["direction_from"],
            "direction_to": block["direction_to"],
            "score": block["score"],
            "stage_counts": block["stage_counts"],
            "evidence": block["evidence"],
            "measured_move": block["measured_move"],
            "citations": block["citations"],
        }

    print(json.dumps(block, indent=2, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Structural regime-change detection (whole-corpus evidence)."
    )
    parser.add_argument("--instrument", default="EUR_USD")
    parser.add_argument("--granularity", default="D")
    parser.add_argument(
        "--count",
        type=int,
        default=250,
        help="Candle count. Ignored when both --from and --to are set.",
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
        "--evidence-only",
        action="store_true",
        help="Print only the staged verdict + evidence (drop full structure).",
    )
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
