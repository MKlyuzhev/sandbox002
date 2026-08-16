"""Stub paper executor: simulate fills from journal pending_exec rows.

Never calls a broker. Run as a separate process:

    python -m agent.executor --once
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent.journal import DEFAULT_DB_PATH, Journal  # noqa: E402
from agent.schema import RunRecord, SimFill  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def simulate_fill(record: RunRecord) -> SimFill:
    """Fill at proposal.entry, else regime last_close. Zero slippage."""
    price = None
    if record.proposal is not None and record.proposal.entry is not None:
        price = float(record.proposal.entry)
    else:
        raw = (record.regime or {}).get("last_close")
        if raw is not None:
            price = float(raw)
    if price is None:
        return SimFill(
            run_id=record.run_id,
            status="rejected",
            fill_price=None,
            ts=_now(),
            note="stub: no entry or last_close",
        )
    return SimFill(
        run_id=record.run_id,
        status="filled_sim",
        fill_price=price,
        ts=_now(),
        note="stub: zero slippage; no broker",
    )


def process_pending(journal: Journal) -> list[SimFill]:
    fills: list[SimFill] = []
    for record in journal.list_pending():
        fill = simulate_fill(record)
        journal.record_fill(fill)
        fills.append(fill)
    return fills


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Stub paper executor. Reads pending journal intents and records "
            "simulated fills. No OANDA or MT4 orders."
        )
    )
    parser.add_argument("--journal", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process current pending rows and exit (default).",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Loop, sleeping --interval seconds between scans.",
    )
    parser.add_argument("--interval", type=float, default=5.0)
    args = parser.parse_args()
    journal = Journal(args.journal)

    def _tick() -> list[SimFill]:
        fills = process_pending(journal)
        print(json.dumps([f.model_dump(mode="json") for f in fills], indent=2))
        return fills

    if args.watch:
        try:
            while True:
                _tick()
                time.sleep(args.interval)
        except KeyboardInterrupt:
            return 0
    _tick()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
