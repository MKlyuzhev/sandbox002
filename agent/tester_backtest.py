"""CLI: python -m agent.tester_backtest --instrument GBP_USD --tf H1 --htf D

Middle step of the MT4 Strategy Tester two-pass back-test. Reads the bars the
``SandboxTesterBridge`` EA exported (``bars.csv``) and writes ``decisions.csv``
for the EA replay pass. Research only; no orders.

Entry engines via ``--engine`` or ``--chapter``:

* ``mtf`` (Ch.8, default): resample HTF from exported LTF; rollover-peak MTF.
* ``dbb`` (Ch.9): single-TF Double Bollinger on exported bars.
* ``perfect_order`` (Ch.16) / ``breakout20`` (Ch.14): single-TF event walks.
* ``fader`` (Ch.13): resample HTF like MTF; first-fire failed-break fades.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent.event_walk import event_decisions  # noqa: E402
from agent.fader_walk import fader_decisions  # noqa: E402
from agent.lien_chapters import EVENT_ENGINES, resolve_engine  # noqa: E402
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
    return decisions, {
        "htf": args.htf,
        "htf_bar_count": len(htf_bars),
        "entry_mode": args.entry_mode,
    }


def _run_fader(args: argparse.Namespace, bars: list, goal: Goal) -> tuple[list, dict]:
    htf_bars = mt4_tester.resample_bars(bars, args.htf)
    decisions = fader_decisions(
        htf_bars,
        bars,
        goal,
        lookback=args.lookback,
        start_index=args.start_index,
    )
    return decisions, {"htf": args.htf, "htf_bar_count": len(htf_bars)}


def _run_event(
    engine: str, args: argparse.Namespace, bars: list, goal: Goal
) -> tuple[list, dict]:
    decisions = event_decisions(
        bars,
        goal,
        engine,
        lookback=args.lookback,
        start_index=args.start_index,
    )
    return decisions, {}


def _run(args: argparse.Namespace) -> int:
    try:
        engine = resolve_engine(chapter=args.chapter, engine=args.engine)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

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

    dual_tf = engine in ("mtf", "fader")
    goal = Goal(
        instrument=args.instrument,
        granularity=args.htf if dual_tf else args.tf,
        ltf_granularity=args.tf,
        mode="paper",
        risk_fraction=args.risk_fraction,
        balance=args.balance,
        no_rag=True,
        no_llm=True,
    )

    try:
        if engine == "mtf":
            decisions, extra = _run_mtf(args, bars, goal)
        elif engine == "fader":
            decisions, extra = _run_fader(args, bars, goal)
        elif engine in EVENT_ENGINES:
            decisions, extra = _run_event(engine, args, bars, goal)
        else:
            print(f"tester_backtest: unsupported engine {engine}", file=sys.stderr)
            return 2
    except (mt4_tester.TesterFeedError, regime_walk.WalkError) as exc:
        print(f"Walk error: {exc}", file=sys.stderr)
        return 1

    mt4_tester.write_decisions_csv(decisions_file, decisions)

    payload = {
        "engine": engine,
        "chapter": args.chapter,
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
            "engine=mtf resamples an HTF (Ch.8); engine=fader resamples HTF (Ch.13); "
            "dbb/breakout20/perfect_order run on the exported TF. Writes decisions.csv. "
            "No orders."
        )
    )
    parser.add_argument("--instrument", required=True)
    parser.add_argument(
        "--engine",
        choices=("mtf", "dbb", "fader", "breakout20", "perfect_order"),
        default=None,
        help="Entry engine (default mtf if --chapter is omitted).",
    )
    parser.add_argument(
        "--chapter",
        type=int,
        default=None,
        help="Lien chapter alias: 8=mtf, 9=dbb, 13=fader, 14=breakout20, 16=perfect_order.",
    )
    parser.add_argument(
        "--tf",
        default="H1",
        help="Timeframe the tester ran (default H1; use D for dbb/14/16).",
    )
    parser.add_argument(
        "--htf",
        default="D",
        help="Higher TF to resample (mtf and fader only; default D).",
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
    if args.engine is None and args.chapter is None:
        args.engine = "mtf"
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
