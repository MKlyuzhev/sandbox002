"""CLI: python -m agent.tester_backtest --instrument GBP_USD --tf H1 --htf D

Middle step of the MT4 Strategy Tester two-pass back-test. Reads the bars the
``SandboxTesterBridge`` EA exported (``bars.csv``), derives the higher timeframe
by resampling, computes rollover-peak MTF decisions (``agent.mtf_walk``), and
writes ``decisions.csv`` for the EA replay pass. Research only; no orders.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent.mtf_walk import mtf_decisions  # noqa: E402
from agent.schema import Goal  # noqa: E402
from app import mt4_tester, regime_walk  # noqa: E402


def _run(args: argparse.Namespace) -> int:
    bars_file = (
        Path(args.bars)
        if args.bars
        else mt4_tester.bars_path(args.instrument, args.tf)
    )
    decisions_file = (
        Path(args.decisions)
        if args.decisions
        else mt4_tester.decisions_path(args.instrument, args.tf)
    )

    try:
        ltf_bars = mt4_tester.read_bars_csv(bars_file)
    except mt4_tester.TesterFeedError as exc:
        print(f"Feed error: {exc}", file=sys.stderr)
        return 2

    htf_bars = mt4_tester.resample_bars(ltf_bars, args.htf)

    goal = Goal(
        instrument=args.instrument,
        granularity=args.htf,
        ltf_granularity=args.tf,
        mode="paper",
        risk_fraction=args.risk_fraction,
        balance=args.balance,
        no_rag=True,
        no_llm=True,
    )

    try:
        decisions = mtf_decisions(
            htf_bars,
            ltf_bars,
            goal,
            lookback=args.lookback,
            start_index=args.start_index,
        )
    except regime_walk.WalkError as exc:
        print(f"Walk error: {exc}", file=sys.stderr)
        return 1

    mt4_tester.write_decisions_csv(decisions_file, decisions)

    payload = {
        "instrument": args.instrument,
        "tf": args.tf,
        "htf": args.htf,
        "lookback": args.lookback,
        "bars_path": str(bars_file),
        "decisions_path": str(decisions_file),
        "ltf_bar_count": len(ltf_bars),
        "htf_bar_count": len(htf_bars),
        "decision_count": len(decisions),
        "first_signal": decisions[0]["signal_time"] if decisions else None,
        "last_signal": decisions[-1]["signal_time"] if decisions else None,
    }
    print(json.dumps(payload, indent=2, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compute the MT4 Strategy Tester decision feed from exported bars: "
            "resample HTF, run rollover-peak MTF, write decisions.csv. No orders."
        )
    )
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--tf", default="H1", help="Lower TF the tester ran (default H1).")
    parser.add_argument("--htf", default="D", help="Higher TF to resample (default D).")
    parser.add_argument(
        "--lookback",
        type=int,
        default=regime_walk.DEFAULT_LOOKBACK,
        help="Causal window length for both timeframes (default 250).",
    )
    parser.add_argument(
        "--start-index",
        type=int,
        default=None,
        help="First LTF bar index to evaluate (default lookback-1).",
    )
    parser.add_argument("--risk-fraction", type=float, default=0.02)
    parser.add_argument("--balance", type=float, default=10_000.0)
    parser.add_argument(
        "--bars",
        default=None,
        help="Override bars.csv path (default tester sandbox).",
    )
    parser.add_argument(
        "--decisions",
        default=None,
        help="Override decisions.csv path (default tester sandbox).",
    )
    args = parser.parse_args()
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
