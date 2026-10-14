#!/usr/bin/env python3
"""MTF entry read: W1/D1/H1 grids, active plans and exposure for one or more pairs.

The data step of the ``/mtf-entry`` skill; see docs/MTF_ENTRY_WORKFLOW.md.
Reads only: it never stores a plan, draws, or places an order.

Examples:
    .venv/bin/python scripts/mtf_entry_read.py EUR_USD
    .venv/bin/python scripts/mtf_entry_read.py EUR_USD USD_JPY --out /tmp/read.json
    .venv/bin/python scripts/mtf_entry_read.py --active     # every pair with an active plan
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import mtf_entry  # noqa: E402
from app.trader_store import TraderStore  # noqa: E402


async def run(instruments: list[str], store: TraderStore) -> dict:
    return {
        "pairs": [await mtf_entry.entry_read(i, store) for i in instruments],
        "exposure": mtf_entry.exposure(store.list_orders(include_closed=False)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("instruments", nargs="*", help="OANDA names, e.g. EUR_USD")
    parser.add_argument("--active", action="store_true", help="add every pair with an active plan")
    parser.add_argument("--out", type=Path, help="write the JSON here instead of stdout")
    args = parser.parse_args()
    store = TraderStore()
    names = [n.upper() for n in args.instruments]
    if args.active:
        names += [n for n in mtf_entry.active_instruments(store) if n not in names]
    if not names:
        parser.error("name a pair (EUR_USD) or pass --active")
    text = json.dumps(asyncio.run(run(names, store)), indent=1, default=str)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({', '.join(names)})")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
