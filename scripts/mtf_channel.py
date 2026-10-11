#!/usr/bin/env python3
"""W1 channel grid (MTF step 1): propose, draw on MT4, and read.

Same code as the MCP tools ``mtf_channel_propose`` / ``mt4_draw_mtf_grid`` /
``mtf_channel_read``. See docs/MTF_CHANNEL_ANALYSIS.md. Research only.

Examples:
    .venv/bin/python scripts/mtf_channel.py USD_JPY                # proposals
    .venv/bin/python scripts/mtf_channel.py USD_JPY --draw          # draw candidate 0
    .venv/bin/python scripts/mtf_channel.py USD_JPY --read chart    # read the trader's grid
    .venv/bin/python scripts/mtf_channel.py USD_JPY --read proposed --candidate 1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import mt4_bridge, mtf_channel, trader_outbox  # noqa: E402


async def run(args: argparse.Namespace) -> dict:
    data = await mtf_channel.load_w1_bars(args.instrument, fetch=not args.no_fetch)
    bars = data["bars"]
    proposal = mtf_channel.propose_channel(bars)
    out: dict = {"proposal": proposal, "oanda_weeks_added": data["oanda_weeks_added"]}
    if data["note"]:
        out["fetch_note"] = data["note"]
    if args.draw:
        grid = mtf_channel.grid_from_proposal(bars, proposal, args.candidate)
        out["draw"] = mtf_channel.draw_grid(args.instrument, grid)
    if args.read == "chart":
        ok, reason = mt4_bridge.check_chart(args.instrument, "W1")
        if not ok:
            raise SystemExit(reason)
        objects = trader_outbox.load_chart(mt4_bridge.chart_dir(args.instrument, "W1"))["objects"]
        grid = mtf_channel.grid_from_chart(bars, objects, args.channel_name)
        out["read"] = mtf_channel.read_grid(bars, grid, instrument=args.instrument)
    elif args.read == "proposed":
        grid = mtf_channel.grid_from_proposal(bars, proposal, args.candidate)
        out["read"] = mtf_channel.read_grid(bars, grid, instrument=args.instrument)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("instrument", help="OANDA name, e.g. USD_JPY")
    parser.add_argument("--candidate", type=int, default=0)
    parser.add_argument("--draw", action="store_true", help="draw the candidate on the live W1 chart")
    parser.add_argument("--read", choices=("chart", "proposed"))
    parser.add_argument("--channel-name", default="")
    parser.add_argument("--no-fetch", action="store_true", help=".hst bars only, no OANDA")
    args = parser.parse_args()
    try:
        out = asyncio.run(run(args))
    except mtf_channel.ChannelError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
