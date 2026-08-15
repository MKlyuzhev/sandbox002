#!/usr/bin/env python3
"""Walk-forward Lien regime test (causal windows only; no look-forward).

Fetches warmup bars ending at --from, then the [--from, --to] range. At each
step the classifier sees only bars[:i+1][-lookback:]. Optional --mt4 paints
on a matching SandboxChartBridge chart (prefix sbox.regime.walk.).
--mt4-show selects ranges, markers, or both.

Examples:
    .venv/bin/python scripts/walk_regime.py \\
      --instrument GBP_USD --granularity D \\
      --from 2024-01-01T00:00:00Z --to 2024-06-01T00:00:00Z --mt4
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import indicators, oanda_client, regime_walk  # noqa: E402


def _merge_warmup_and_test(warmup: list[dict], test: list[dict]) -> list[dict]:
    return regime_walk.prepare_bars(list(warmup) + list(test))


async def _fetch_bars(
    instrument: str,
    granularity: str,
    from_time: str,
    to_time: str,
    lookback: int,
) -> list[dict]:
    if lookback > regime_walk.MAX_BARS:
        raise regime_walk.WalkError(
            f"lookback {lookback} exceeds OANDA max {regime_walk.MAX_BARS}"
        )
    warmup_payload = await oanda_client.get_candles(
        instrument,
        granularity=granularity,
        count=lookback,
        price="M",
        to_time=from_time,
    )
    test_payload = await oanda_client.get_candles(
        instrument,
        granularity=granularity,
        count=None,
        price="M",
        from_time=from_time,
        to_time=to_time,
    )
    warmup = oanda_client.candles_to_bars(warmup_payload, prefer="mid")
    test = oanda_client.candles_to_bars(test_payload, prefer="mid")
    if len(test) >= regime_walk.MAX_BARS:
        raise regime_walk.WalkError(
            f"test range returned {len(test)} bars; OANDA max is "
            f"{regime_walk.MAX_BARS}. Shrink --from/--to."
        )
    bars = _merge_warmup_and_test(warmup, test)
    bars = regime_walk.drop_after(bars, to_time)
    return bars


async def run(args: argparse.Namespace) -> int:
    try:
        bars = await _fetch_bars(
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
        result = regime_walk.walk_and_collapse(
            bars,
            lookback=args.lookback,
            step=args.step,
            start_index=start_index,
            horizon=args.horizon,
            min_n=args.min_n,
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
        "step": args.step,
        "horizon": args.horizon,
        "min_n": args.min_n,
        "mt4_show": args.mt4_show,
        "bar_count": len(bars),
        "start_index": start_index,
        "summary": result["summary"],
        "runs": result["runs"],
        "steps": result["steps"],
    }

    if args.mt4:
        from app import mt4_bridge  # noqa: E402

        drawn = mt4_bridge.apply_regime_walk(
            result,
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

    print(json.dumps(payload, indent=2, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Causal walk-forward Lien regime test. "
            "No look-forward: each step uses only bars up to that close."
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
    parser.add_argument(
        "--step",
        type=int,
        default=1,
        help="Advance the right edge by this many bars (default 1).",
    )
    parser.add_argument(
        "--horizon",
        type=int,
        default=regime_walk.DEFAULT_HORIZON,
        help="Steps ahead for empirical p_hat / delayed eval (default 5).",
    )
    parser.add_argument(
        "--min-n",
        dest="min_n",
        type=int,
        default=regime_walk.DEFAULT_MIN_N,
        help="Minimum same-bucket past episodes before p_hat is defined (default 10).",
    )
    parser.add_argument(
        "--phat-watch",
        type=float,
        default=regime_walk.DEFAULT_PHAT_WATCH,
        help="MT4 change-watch if p_hat >= this (default 0.5).",
    )
    parser.add_argument(
        "--instability-watch",
        type=float,
        default=regime_walk.DEFAULT_INSTABILITY_WATCH,
        help="MT4 change-watch if instability >= this (default 0.6).",
    )
    parser.add_argument(
        "--mt4",
        action="store_true",
        help="Draw on MT4 (prefix sbox.regime.walk.). Use --mt4-show to pick layers.",
    )
    parser.add_argument(
        "--mt4-show",
        choices=("ranges", "markers", "both"),
        default="both",
        help="MT4 layers: regime-run ranges, change-watch markers, or both (default).",
    )
    parser.add_argument(
        "--mt4-prefix",
        default="sbox.regime.walk.",
        help="Object name prefix for --mt4 (default: sbox.regime.walk.).",
    )
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
