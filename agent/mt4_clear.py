"""CLI: python -m agent.mt4_clear [--instrument EUR_USD] [--granularity D] [--prefix sbox.]

Delete sandbox chart objects on the matching chart's inbox
(`sandbox002/<SYMBOL>_<TF>/`). Objects are created hidden / non-selectable,
so the MT4 Object List cannot remove them (it only deselects). This writes
op=clear for that chart's EA. Display only; no orders.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app import mt4_bridge  # noqa: E402
from app.mt4_bridge import DEFAULT_PREFIX  # noqa: E402

_PREFIX = re.compile(r"^sbox\.[a-zA-Z0-9._-]*$")


def parse_prefix(value: str) -> str:
    text = value.strip()
    if not _PREFIX.match(text):
        raise argparse.ArgumentTypeError("prefix must start with sbox.")
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Remove sandbox objects (sbox.*) from one EA chart inbox. "
            "Research display only; no broker orders."
        )
    )
    parser.add_argument(
        "--instrument",
        default="EUR_USD",
        help="OANDA instrument whose chart folder to clear (default: EUR_USD).",
    )
    parser.add_argument(
        "--granularity",
        default="D",
        help="Timeframe whose chart folder to clear (default: D).",
    )
    parser.add_argument(
        "--prefix",
        default=DEFAULT_PREFIX,
        type=parse_prefix,
        help="Name prefix to delete (default: sbox. — all sandbox layers).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="No JSON on stdout (still prints errors on failure).",
    )
    args = parser.parse_args(argv)
    result = mt4_bridge.clear_layer(
        args.prefix,
        symbol=args.instrument,
        timeframe=args.granularity,
    )
    if not args.quiet:
        print(json.dumps(result, indent=2, default=str))
    if not result.get("ok"):
        print(result.get("error", "clear failed"), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
