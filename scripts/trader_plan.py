#!/usr/bin/env python3
"""Journal plans from the command line: store, set status, link, draw levels.

The approval step of the ``/mtf-entry`` skill; see docs/MTF_ENTRY_WORKFLOW.md.
Same code as the dashboard's Plans tab and the ``trader_add_analysis`` MCP
tool. Nothing here places, modifies, or deletes an MT4 order.

Examples:
    .venv/bin/python scripts/trader_plan.py store plan.json   # prints the new id
    .venv/bin/python scripts/trader_plan.py status 6 cancelled
    .venv/bin/python scripts/trader_plan.py link 8 362759464
    .venv/bin/python scripts/trader_plan.py draw 8            # levels on the live chart

plan.json keys: instrument, body_md (or body_file), timeframe, order, levels,
refs, ticket, supersedes (marks that plan superseded), kind (default
pre_trade), model.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import trader_journal  # noqa: E402
from app.trader_store import TraderStore  # noqa: E402


def _summary(row: dict) -> dict:
    keys = ("id", "symbol", "status", "ticket", "attach_reason", "supersedes", "order")
    return {k: row.get(k) for k in keys}


def store_plan(store: TraderStore, path: Path) -> dict:
    spec = json.loads(path.read_text(encoding="utf-8"))
    body = spec.get("body_md")
    if not body and spec.get("body_file"):
        body = (path.parent / spec["body_file"]).read_text(encoding="utf-8")
    row = trader_journal.add_analysis(
        store,
        instrument=spec["instrument"],
        kind=spec.get("kind", "pre_trade"),
        body_md=body or "",
        timeframe=spec.get("timeframe", ""),
        ticket=spec.get("ticket"),
        model=spec.get("model", ""),
        refs=spec.get("refs"),
        levels=spec.get("levels"),
        order=spec.get("order"),
        supersedes=spec.get("supersedes"),
    )
    out = _summary(row)
    if spec.get("supersedes"):
        out["superseded"] = _summary(store.get_analysis(int(spec["supersedes"])) or {})
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("store", help="store a plan (or analysis) from a JSON spec")
    p.add_argument("spec", type=Path)
    p = sub.add_parser("status", help="active | superseded | cancelled")
    p.add_argument("id", type=int)
    p.add_argument("status")
    p = sub.add_parser("link", help="attach a plan to an MT4 ticket")
    p.add_argument("id", type=int)
    p.add_argument("ticket", type=int)
    p = sub.add_parser("draw", help="draw a plan's levels on the live chart")
    p.add_argument("id", type=int)
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args()
    store = TraderStore(args.db)
    try:
        if args.cmd == "store":
            out = store_plan(store, args.spec)
        elif args.cmd == "status":
            out = _summary(trader_journal.set_plan_status(store, args.id, args.status))
        elif args.cmd == "link":
            out = _summary(trader_journal.link_plan(store, args.id, args.ticket))
        else:
            out = trader_journal.draw_plan(store, args.id)
    except (trader_journal.TraderJournalError, KeyError, ValueError) as exc:
        raise SystemExit(f"error: {exc}") from exc
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
