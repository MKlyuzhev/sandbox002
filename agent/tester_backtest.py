"""CLI: python -m agent.tester_backtest --instrument GBP_USD --tf H1 --htf D

Middle step of the MT4 Strategy Tester two-pass back-test. Reads the bars the
``SandboxTesterBridge`` EA exported (``bars.csv``) and writes ``decisions.csv``
for the EA replay pass. Research only; no orders.

Two entry engines are selectable with ``--engine``:

* ``mtf`` (Ch.8, default): derives the higher timeframe by resampling the
  exported LTF bars and computes rollover-peak MTF decisions (``agent.mtf_walk``).
* ``dbb`` (Ch.9): single-timeframe Double Bollinger Bands run directly on the
  exported bars (default ``--tf D``); no resample (``agent.dbb_walk``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent.dbb_walk import dbb_decisions  # noqa: E402
from agent.mtf_walk import mtf_decisions  # noqa: E402
from agent.schema import Goal  # noqa: E402
from app import mt4_tester, regime_walk  # noqa: E402


def _run_mtf(args: argparse.Namespace, bars: list, goal: Goal) -> tuple[list, dict]:
    htf_bars = mt4_tester.resample_bars(bars, args.htf)
    decisions = mtf_decisions(
        htf_bars,
        bars,
        goal,
        lookback=args.lookback,
        start_index=args.start_index,
        entry_mode=args.entry_mode,
    )
    return decisions, {"htf": args.htf, "htf_bar_count": len(htf_bars), "entry_mode": args.entry_mode}


def _run_dbb(args: argparse.Namespace, bars: list, goal: Goal) -> tuple[list, dict]:
    decisions = dbb_decisions(
        bars,
        goal,
        lookback=args.lookback,
        start_index=args.start_index,
    )
    return decisions, {}


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
        bars = mt4_tester.read_bars_csv(bars_file)
    except mt4_tester.TesterFeedError as exc:
        print(f"Feed error: {exc}", file=sys.stderr)
        return 2

    goal = Goal(
        instrument=args.instrument,
        granularity=args.htf if args.engine == "mtf" else args.tf,
        ltf_granularity=args.tf,
        mode="paper",
        risk_fraction=args.risk_fraction,
        balance=args.balance,
        no_rag=True,
        no_llm=True,
    )

    try:
        if args.engine == "mtf":
            decisions, extra = _run_mtf(args, bars, goal)
        else:
            decisions, extra = _run_dbb(args, bars, goal)
    except (mt4_tester.TesterFeedError, regime_walk.WalkError) as exc:
        print(f"Walk error: {exc}", file=sys.stderr)
        return 1

    mt4_tester.write_decisions_csv(decisions_file, decisions)

    payload = {
        "engine": args.engine,
        "instrument": args.instrument,
        "tf": args.tf,
        "lookback": args.lookback,
        "bars_path": str(bars_file),
        "decisions_path": str(decisions_file),
        "bar_count": len(bars),
        "decision_count": len(decisions),
        "first_signal": decisions[0]["signal_time"] if decisions else None,
        "last_signal": decisions[-1]["signal_time"] if decisions else None,
        **extra,
    }
    print(json.dumps(payload, indent=2, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compute the MT4 Strategy Tester decision feed from exported bars. "
            "engine=mtf resamples an HTF and runs rollover-peak MTF (Ch.8); "
            "engine=dbb runs Double Bollinger Bands on the exported TF (Ch.9). "
            "Writes decisions.csv. No orders."
        )
    )
    parser.add_argument("--instrument", required=True)
    parser.add_argument(
        "--engine",
        choices=("mtf", "dbb"),
        default="mtf",
        help="Entry engine: mtf (Ch.8, default) or dbb (Ch.9 Double Bollinger).",
    )
    parser.add_argument(
        "--tf",
        default="H1",
        help="Timeframe the tester ran (default H1; use D for dbb).",
    )
    parser.add_argument(
        "--htf",
        default="D",
        help="Higher TF to resample (mtf only; default D).",
    )
    parser.add_argument(
        "--entry-mode",
        choices=("peak", "first_fire"),
        default="peak",
        help=(
            "MTF entry timing (mtf only): peak=rollover-peak confirmation "
            "(default); first_fire=first bar of each firing run."
        ),
    )
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
