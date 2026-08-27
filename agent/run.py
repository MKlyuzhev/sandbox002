"""CLI: python -m agent.run --instrument GBP_USD"""

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

from agent.graph import run  # noqa: E402
from agent.journal import DEFAULT_DB_PATH, Journal  # noqa: E402
from agent.schema import Goal  # noqa: E402


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
    engines = None
    if args.engines:
        engines = [int(x) for x in args.engines.split(",") if x.strip()]
    goal = Goal(
        instrument=args.instrument,
        granularity=args.granularity,
        ltf_granularity=args.ltf_granularity,
        engines=engines,
        mode=args.mode,
        count=args.count,
        from_time=args.from_time,
        to_time=args.to_time,
        risk_fraction=args.risk_fraction,
        balance=args.balance,
        exposure_cap=args.exposure_cap,
        mt4=args.mt4,
        mt4_prefix=args.mt4_prefix,
        no_rag=args.no_rag,
        no_llm=args.no_llm,
        use_account=args.use_account,
        source_filter=args.source,
        top_k=args.top_k,
    )
    journal = None if args.no_journal else Journal(args.journal)
    record = await run(goal, journal=journal)
    print(json.dumps(record.model_dump(mode="json"), indent=2, default=str))
    if record.error and record.action == "wait":
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Lien analysis orchestrator: regime → retrieve → propose → risk gate "
            "→ journal. Research / paper-journal only; no broker orders."
        )
    )
    parser.add_argument("--instrument", default="EUR_USD")
    parser.add_argument("--granularity", default="D")
    parser.add_argument(
        "--ltf-granularity",
        default="H1",
        help="Lower timeframe for multi-TF engines like Ch.8 MTF (default: H1).",
    )
    parser.add_argument(
        "--engines",
        default=None,
        metavar="CHAPTERS",
        help="Comma-separated chapter allow-list (e.g. 8,7). Default: all matching.",
    )
    parser.add_argument("--count", type=int, default=250)
    parser.add_argument("--from", dest="from_time", default=None, metavar="RFC3339")
    parser.add_argument("--to", dest="to_time", default=None, metavar="RFC3339")
    parser.add_argument(
        "--mode",
        choices=("signal", "paper"),
        default="signal",
        help="signal logs setups; paper queues stub fills (no broker).",
    )
    parser.add_argument("--balance", type=float, default=10_000.0)
    parser.add_argument("--risk-fraction", type=float, default=0.02)
    parser.add_argument("--exposure-cap", type=float, default=0.06)
    parser.add_argument(
        "--use-account",
        action="store_true",
        help="Read OANDA practice NAV/balance instead of --balance.",
    )
    parser.add_argument("--mt4", action="store_true")
    parser.add_argument("--mt4-prefix", default="sbox.regime.")
    parser.add_argument("--no-rag", action="store_true")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--source", default="lien-fx")
    parser.add_argument("--top-k", type=int, default=5)
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
