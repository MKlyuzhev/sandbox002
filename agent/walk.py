"""CLI: python -m agent.walk --instrument GBP_USD --from ... --to ...

Causal paper walk: warmup before --from, one position at a time, fill at
decision-bar close, outcome on later bars (stop / target / window_end).
No RAG, no LLM, no broker orders.
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
from agent.paper_walk import walk_paper  # noqa: E402
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
    logger = logging.getLogger("agent.walk")
    try:
        bars = await fetch_walk_bars(
            args.instrument,
            args.granularity,
            args.from_time,
            args.to_time,
            args.lookback,
        )
    except oanda_client.OandaError as exc:
        print(f"OANDA error: {exc}", file=sys.stderr)
        return 2
    except regime_walk.WalkError as exc:
        print(f"Walk error: {exc}", file=sys.stderr)
        return 1

    try:
        start_index = regime_walk.first_index_on_or_after(bars, args.from_time)
        collapsed = regime_walk.walk_and_collapse(
            bars,
            lookback=args.lookback,
            step=1,
            start_index=start_index,
            horizon=args.horizon,
            min_n=args.min_n,
        )
        goal = Goal(
            instrument=args.instrument,
            granularity=args.granularity,
            mode="paper",
            from_time=args.from_time,
            to_time=args.to_time,
            risk_fraction=args.risk_fraction,
            balance=args.balance,
            exposure_cap=args.exposure_cap,
            mt4=args.mt4,
            mt4_prefix=args.mt4_prefix,
            no_rag=True,
            no_llm=True,
        )
        journal = None if args.no_journal else Journal(args.journal)
        logger.info(
            "paper walk %s %s bars=%s start=%s lookback=%s",
            args.instrument,
            args.granularity,
            len(bars),
            start_index,
            args.lookback,
        )
        result = walk_paper(
            bars,
            goal,
            lookback=args.lookback,
            start_index=start_index,
            journal=journal,
        )
    except (regime_walk.WalkError, indicators.IndicatorError) as exc:
        print(f"Walk error: {exc}", file=sys.stderr)
        return 1

    payload = {
        "instrument": args.instrument,
        "granularity": args.granularity,
        "from_time": args.from_time,
        "to_time": args.to_time,
        "lookback": args.lookback,
        "bar_count": len(bars),
        "start_index": start_index,
        "summary": collapsed["summary"],
        "runs": collapsed["runs"],
        "walk_id": result.walk_id,
        "equity": result.equity.model_dump(mode="json"),
        "trades": [t.model_dump(mode="json") for t in result.trades],
        "trade_count": len(result.trades),
    }

    if args.mt4:
        from app import mt4_bridge  # noqa: E402

        drawn = mt4_bridge.apply_regime_walk(
            collapsed,
            args.instrument,
            args.granularity,
            prefix=args.mt4_prefix,
            phat_watch=args.phat_watch,
            instability_watch=args.instability_watch,
            show=args.mt4_show,
        )
        payload["mt4"] = {k: v for k, v in drawn.items() if k != "analysis"}
        if not drawn.get("ok"):
            print(f"MT4 error: {drawn.get('error')}", file=sys.stderr)
            print(json.dumps(payload, indent=2, default=str))
            return 1
        tickets = mt4_bridge.apply_paper_walk_tickets(
            [t.model_dump(mode="json") for t in result.trades],
            args.instrument,
            args.granularity,
            prefix=args.mt4_ticket_prefix,
        )
        payload["mt4_tickets"] = {k: v for k, v in tickets.items() if k != "analysis"}
        if not tickets.get("ok"):
            print(f"MT4 ticket error: {tickets.get('error')}", file=sys.stderr)
            print(json.dumps(payload, indent=2, default=str))
            return 1

    print(json.dumps(payload, indent=2, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Causal paper walk: one Ch.7 ticket at a time, fill at close, "
            "outcome on later bars. Research only; no broker orders."
        )
    )
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--from", dest="from_time", required=True, metavar="RFC3339")
    parser.add_argument("--to", dest="to_time", required=True, metavar="RFC3339")
    parser.add_argument("--granularity", default="D")
    parser.add_argument(
        "--lookback",
        type=int,
        default=regime_walk.DEFAULT_LOOKBACK,
        help="Causal window length (default 250). Warmup is fetched before --from.",
    )
    parser.add_argument("--balance", type=float, default=10_000.0)
    parser.add_argument("--risk-fraction", type=float, default=0.02)
    parser.add_argument("--exposure-cap", type=float, default=0.06)
    parser.add_argument(
        "--horizon",
        type=int,
        default=regime_walk.DEFAULT_HORIZON,
        help="Steps ahead for walk overlay p_hat (default 5).",
    )
    parser.add_argument(
        "--min-n",
        dest="min_n",
        type=int,
        default=regime_walk.DEFAULT_MIN_N,
        help="Minimum same-bucket episodes before p_hat is defined (default 10).",
    )
    parser.add_argument(
        "--phat-watch",
        type=float,
        default=regime_walk.DEFAULT_PHAT_WATCH,
    )
    parser.add_argument(
        "--instability-watch",
        type=float,
        default=regime_walk.DEFAULT_INSTABILITY_WATCH,
    )
    parser.add_argument("--mt4", action="store_true")
    parser.add_argument(
        "--mt4-show",
        choices=("ranges", "markers", "both"),
        default="both",
    )
    parser.add_argument(
        "--mt4-prefix",
        default="sbox.regime.walk.",
        help="Regime-walk overlay prefix (default: sbox.regime.walk.).",
    )
    parser.add_argument(
        "--mt4-ticket-prefix",
        default="sbox.ticket.walk.",
        help="Paper-ticket marker prefix (default: sbox.ticket.walk.).",
    )
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
