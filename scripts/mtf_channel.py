#!/usr/bin/env python3
"""Channel grid (MTF step 1 on W1, step 2 on D1): propose, draw on MT4, and read.

Same code as the MCP tools ``mtf_channel_propose`` / ``mt4_draw_mtf_grid`` /
``mtf_channel_read``. See docs/MTF_CHANNEL_ANALYSIS.md. Research only.

Examples:
    .venv/bin/python scripts/mtf_channel.py USD_JPY                # proposals
    .venv/bin/python scripts/mtf_channel.py USD_JPY --draw          # draw candidate 0
    .venv/bin/python scripts/mtf_channel.py USD_JPY --read chart    # read the trader's grid
    .venv/bin/python scripts/mtf_channel.py USD_JPY --read proposed --candidate 1
    .venv/bin/python scripts/mtf_channel.py USD_JPY --tf D1             # D1 legs + proposals
    .venv/bin/python scripts/mtf_channel.py USD_JPY --tf D1 --draw      # previous + current leg
    .venv/bin/python scripts/mtf_channel.py USD_JPY --tf D1 --read chart --leg previous
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
    tf = mtf_channel.get_tf(args.tf)
    data = await mtf_channel.load_bars(args.instrument, tf, fetch=not args.no_fetch)
    bars = data["bars"]
    legs = None
    if tf.origin == "w1_legs":
        legs = await mtf_channel.d1_leg_proposals(args.instrument, bars, fetch=not args.no_fetch)
        out: dict = {"proposal": legs}
    else:
        proposal = mtf_channel.propose_channel(bars, tf)
        out = {"proposal": proposal}
    if tf is mtf_channel.W1:
        out["oanda_weeks_added"] = data["oanda_weeks_added"]
    else:
        out["oanda_bars_added"] = data["oanda_bars_added"]
    if data["note"]:
        out["fetch_note"] = data["note"]
    if args.draw and legs:
        out["draw"] = [
            mtf_channel.draw_grid(args.instrument, mtf_channel.grid_from_leg(bars, legs, name, cand))
            for name, cand in (("previous", args.previous_candidate), ("current", args.candidate))
            if args.leg in ("both", name)
        ]
    elif args.draw:
        grid = mtf_channel.grid_from_proposal(bars, proposal, args.candidate, tf)
        out["draw"] = mtf_channel.draw_grid(args.instrument, grid)
    if args.read == "chart":
        ok, reason = mt4_bridge.check_chart(args.instrument, tf.name)
        if not ok:
            raise SystemExit(reason)
        objects = trader_outbox.load_chart(mt4_bridge.chart_dir(args.instrument, tf.name))["objects"]
        if legs:
            leg = "previous" if args.leg == "previous" else "current"
            grid = mtf_channel.leg_grid_from_chart(
                bars, objects, args.instrument, legs, leg, args.channel_name
            )
        else:
            grid = mtf_channel.grid_from_chart(bars, objects, args.channel_name, tf)
        out["read"] = mtf_channel.read_grid(bars, grid, instrument=args.instrument)
    elif args.read == "proposed" and legs:
        leg = "previous" if args.leg == "previous" else "current"
        grid = mtf_channel.grid_from_leg(bars, legs, leg, args.candidate)
        out["read"] = mtf_channel.read_grid(bars, grid, instrument=args.instrument)
    elif args.read == "proposed":
        grid = mtf_channel.grid_from_proposal(bars, proposal, args.candidate, tf)
        out["read"] = mtf_channel.read_grid(bars, grid, instrument=args.instrument)
    if args.read and legs:
        leg = "previous" if args.leg == "previous" else "current"
        out["read"] = {"leg": legs[leg]["leg"], **out["read"]}
    if args.read and tf is not mtf_channel.W1:
        out["w1_context"] = await mtf_channel.w1_context(args.instrument, fetch=not args.no_fetch)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("instrument", help="OANDA name, e.g. USD_JPY")
    parser.add_argument("--tf", default="W1", choices=sorted(mtf_channel.TFS), help="timeframe (default W1)")
    parser.add_argument("--candidate", type=int, default=0)
    parser.add_argument("--leg", default="both", choices=("both", "current", "previous"),
                        help="D1: which leg to draw (default both) or read (default current)")
    parser.add_argument("--previous-candidate", type=int, default=0, help="D1 previous leg's candidate")
    parser.add_argument("--draw", action="store_true", help="draw the candidate on the live chart of --tf")
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
