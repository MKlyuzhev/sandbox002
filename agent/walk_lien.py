"""CLI: python -m agent.walk_lien --chapter 16 --instrument GBP_USD --from ... --to ...

Causal paper walk for encoded Lien chapters 9 / 13 / 14 / 16. Single-TF event
engines (9, 14, 16) step the exported granularity; Ch.13 Fader steps H1 against
a daily ADX gate. Research only; no broker orders.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent.journal import DEFAULT_DB_PATH, Journal  # noqa: E402
from agent.walk_exec import add_fill_cli_args  # noqa: E402
from agent.walk_jobs import (
    WalkJobError,
    WalkRuntime,
    compact_walk_payload,
    execute_walk,
)  # noqa: E402
from app import oanda_client, regime_walk  # noqa: E402


def _configure_logging(quiet: bool) -> None:
    if quiet:
        return
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )


async def _async_main(args: argparse.Namespace) -> int:
    logger = logging.getLogger("agent.walk_lien")
    journal = None if args.no_journal else Journal(args.journal)
    try:
        result, meta = await execute_walk(
            "lien",
            args.instrument,
            args.from_time,
            args.to_time,
            chapter=args.chapter,
            granularity=args.granularity,
            ltf_granularity=args.ltf_granularity,
            lookback=args.lookback,
            fill_mode=args.fill_mode,
            balance=args.balance,
            risk_fraction=args.risk_fraction,
            exposure_cap=args.exposure_cap,
            value_per_price_unit=args.value_per_price_unit,
            journal=journal,
            no_journal=args.no_journal,
        )
    except oanda_client.OandaError as exc:
        print(f"OANDA error: {exc}", file=sys.stderr)
        return 2
    except (WalkJobError, WalkRuntime) as exc:
        print(f"Walk error: {exc}", file=sys.stderr)
        return 2 if "not encoded" in str(exc) else 1

    logger.info(
        "lien walk ch%s %s start=%s",
        args.chapter,
        args.instrument,
        meta.get("start_index"),
    )
    payload = compact_walk_payload(result, meta, truncate=False)
    print(json.dumps(payload, indent=2, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Causal Lien paper walk for chapters 9, 13, 14, or 16. "
            "Research only; no broker orders."
        )
    )
    parser.add_argument(
        "--chapter",
        type=int,
        required=True,
        help="9 (dbb), 13 (fader), 14 (breakout20), or 16 (perfect_order).",
    )
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--from", dest="from_time", required=True, metavar="RFC3339")
    parser.add_argument("--to", dest="to_time", required=True, metavar="RFC3339")
    parser.add_argument("--granularity", default="D", help="Primary/HTF (default D).")
    parser.add_argument(
        "--ltf-granularity",
        default="H1",
        help="Lower TF for Ch.13 (default H1).",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=regime_walk.DEFAULT_LOOKBACK,
        help="Causal window length (default 250). Warmup is fetched before --from.",
    )
    parser.add_argument("--balance", type=float, default=10_000.0)
    parser.add_argument("--risk-fraction", type=float, default=0.02)
    parser.add_argument("--exposure-cap", type=float, default=0.06)
    add_fill_cli_args(parser)
    parser.add_argument("--journal", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--no-journal", action="store_true")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="No progress on stderr (stdout JSON only).",
    )
    args = parser.parse_args()
    _configure_logging(args.quiet)
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
