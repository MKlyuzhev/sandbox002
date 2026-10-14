#!/usr/bin/env python3
"""Show or switch the timeframe of a pair's live MT4 chart (EA v1.11 ``set_period``).

One chart per pair carries the W1/D1/H1 grids; the EA draws only on the
period its chart shows, so drawing switches it. Never touches orders.

Examples:
    .venv/bin/python scripts/mt4_period.py GBP_USD        # which timeframe is live
    .venv/bin/python scripts/mt4_period.py GBP_USD H1     # switch the chart to H1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import mt4_bridge  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("instrument", help="OANDA name, e.g. GBP_USD")
    parser.add_argument("timeframe", nargs="?", help="W1, D1, H1, ... (omit to show the live one)")
    args = parser.parse_args()
    if not args.timeframe:
        fresh = mt4_bridge._fresh_timeframes(args.instrument)
        print(json.dumps({"instrument": args.instrument, "live": fresh}))
        return 0 if fresh else 1
    out = mt4_bridge.set_period(args.instrument, args.timeframe)
    print(json.dumps(out))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
