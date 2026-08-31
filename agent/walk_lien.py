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

from agent.event_walk import walk_event  # noqa: E402
from agent.fader_walk import walk_fader  # noqa: E402
from agent.journal import DEFAULT_DB_PATH, Journal  # noqa: E402
from agent.lien_chapters import CHAPTER_TO_ENGINE, EVENT_ENGINES  # noqa: E402
from agent.schema import Goal  # noqa: E402
from app import indicators, oanda_client, regime_walk  # noqa: E402
from app.walk_fetch import fetch_walk_bars  # noqa: E402


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
    engine = CHAPTER_TO_ENGINE.get(args.chapter)
    if engine is None:
        print(
            f"walk_lien: chapter {args.chapter} is not encoded "
            "(use 9, 13, 14, or 16).",
            file=sys.stderr,
        )
        return 2

    journal = None if args.no_journal else Journal(args.journal)

    try:
        if engine == "fader":
            htf_bars, ltf_bars = await asyncio.gather(
                fetch_walk_bars(
                    args.instrument,
                    args.granularity,
                    args.from_time,
                    args.to_time,
                    args.lookback,
                ),
                fetch_walk_bars(
                    args.instrument,
                    args.ltf_granularity,
                    args.from_time,
                    args.to_time,
                    args.lookback,
                ),
            )
            start_index = regime_walk.first_index_on_or_after(
                ltf_bars, args.from_time
            )
            goal = Goal(
                instrument=args.instrument,
                granularity=args.granularity,
                ltf_granularity=args.ltf_granularity,
                mode="paper",
                from_time=args.from_time,
                to_time=args.to_time,
                risk_fraction=args.risk_fraction,
                balance=args.balance,
                exposure_cap=args.exposure_cap,
                no_rag=True,
                no_llm=True,
            )
            logger.info(
                "fader walk %s htf=%s ltf=%s start=%s",
                args.instrument,
                args.granularity,
                args.ltf_granularity,
                start_index,
            )
            result = walk_fader(
                htf_bars,
                ltf_bars,
                goal,
                lookback=args.lookback,
                start_index=start_index,
                journal=journal,
            )
            payload = {
                "chapter": args.chapter,
                "engine": engine,
                "instrument": args.instrument,
                "granularity": args.granularity,
                "ltf_granularity": args.ltf_granularity,
                "htf_bar_count": len(htf_bars),
                "ltf_bar_count": len(ltf_bars),
                "from_time": args.from_time,
                "to_time": args.to_time,
                "lookback": args.lookback,
                "start_index": start_index,
                "walk_id": result.walk_id,
                "equity": result.equity.model_dump(mode="json"),
                "trades": [t.model_dump(mode="json") for t in result.trades],
                "trade_count": len(result.trades),
            }
        else:
            if engine not in EVENT_ENGINES:
                print(f"walk_lien: engine {engine} is not an event walk", file=sys.stderr)
                return 2
            bars = await fetch_walk_bars(
                args.instrument,
                args.granularity,
                args.from_time,
                args.to_time,
                args.lookback,
            )
            start_index = regime_walk.first_index_on_or_after(bars, args.from_time)
            goal = Goal(
                instrument=args.instrument,
                granularity=args.granularity,
                mode="paper",
                from_time=args.from_time,
                to_time=args.to_time,
                risk_fraction=args.risk_fraction,
                balance=args.balance,
                exposure_cap=args.exposure_cap,
                no_rag=True,
                no_llm=True,
            )
            logger.info(
                "event walk ch%s %s %s bars=%s start=%s",
                args.chapter,
                args.instrument,
                args.granularity,
                len(bars),
                start_index,
            )
            result = walk_event(
                bars,
                goal,
                engine,
                lookback=args.lookback,
                start_index=start_index,
                journal=journal,
            )
            payload = {
                "chapter": args.chapter,
                "engine": engine,
                "instrument": args.instrument,
                "granularity": args.granularity,
                "bar_count": len(bars),
                "from_time": args.from_time,
                "to_time": args.to_time,
                "lookback": args.lookback,
                "start_index": start_index,
                "walk_id": result.walk_id,
                "equity": result.equity.model_dump(mode="json"),
                "trades": [t.model_dump(mode="json") for t in result.trades],
                "trade_count": len(result.trades),
            }
    except oanda_client.OandaError as exc:
        print(f"OANDA error: {exc}", file=sys.stderr)
        return 2
    except (regime_walk.WalkError, indicators.IndicatorError) as exc:
        print(f"Walk error: {exc}", file=sys.stderr)
        return 1

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
