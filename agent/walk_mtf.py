"""CLI: python -m agent.walk_mtf --instrument GBP_USD --from ... --to ...

Causal Ch.8 MTF paper walk: dual-TF warmup, rollover-peak entry on the LTF,
fill at peak-bar close, outcome on later LTF bars. Research only; no broker orders.
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
    logger = logging.getLogger("agent.walk_mtf")
    journal = None if args.no_journal else Journal(args.journal)
    try:
        result, meta = await execute_walk(
            "mtf",
            args.instrument,
            args.from_time,
            args.to_time,
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
            entry_mode=args.entry_mode,
        )
    except oanda_client.OandaError as exc:
        print(f"OANDA error: {exc}", file=sys.stderr)
        return 2
    except (WalkJobError, WalkRuntime) as exc:
        print(f"Walk error: {exc}", file=sys.stderr)
        return 1

    logger.info(
        "mtf walk %s htf=%s ltf=%s htf_bars=%s ltf_bars=%s start=%s lookback=%s",
        args.instrument,
        args.granularity,
        args.ltf_granularity,
        meta.get("htf_bar_count"),
        meta.get("ltf_bar_count"),
        meta.get("start_index"),
        args.lookback,
    )
    payload = compact_walk_payload(result, meta, truncate=False)
    print(json.dumps(payload, indent=2, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Causal Ch.8 MTF paper walk: rollover-peak entry on the lower TF, "
            "fill at peak-bar close, outcome on later bars. Research only."
        )
    )
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--from", dest="from_time", required=True, metavar="RFC3339")
    parser.add_argument("--to", dest="to_time", required=True, metavar="RFC3339")
    parser.add_argument("--granularity", default="D", help="Higher TF (default D).")
    parser.add_argument(
        "--ltf-granularity",
        default="H1",
        help="Lower TF stepped by the walk (default H1).",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=regime_walk.DEFAULT_LOOKBACK,
        help="Causal window length (default 250). Warmup is fetched before --from.",
    )
    parser.add_argument(
        "--entry-mode",
        choices=("peak", "first_fire"),
        default="first_fire",
        help=(
            "Entry timing: peak=rollover-peak confirmation (default); "
            "first_fire=first bar of each firing run."
        ),
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
