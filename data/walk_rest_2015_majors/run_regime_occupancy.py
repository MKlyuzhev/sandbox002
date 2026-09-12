"""Causal Ch.7 occupancy for the rest-fill majors set. No steps dump."""

from __future__ import annotations

import asyncio
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from app import indicators, oanda_client, regime_walk  # noqa: E402
from app.walk_fetch import fetch_walk_bars  # noqa: E402

OUT = Path(__file__).resolve().parent
FROM_T = "2015-01-01T00:00:00Z"
TO_T = "2026-09-10T00:00:00Z"
PAIRS = ["USD_JPY", "GBP_USD", "EUR_USD", "EUR_JPY", "AUD_USD", "USD_CHF"]
EXISTING = {
    "USD_JPY": _REPO / "data/usd_jpy_regime_walk_2015_2026.json",
    "GBP_USD": _REPO / "data/gbp_usd_regime_walk_2015_2026.json",
}


def _year(ts: str) -> str:
    return str(ts)[:4]


def _parse(ts: str) -> datetime:
    text = str(ts).replace("Z", "+00:00")
    if "." in text:
        head, rest = text.split(".", 1)
        tz = "+" if "+" in rest else ("-" if "-" in rest[1:] else "")
        if tz:
            idx = rest.find(tz, 1) if tz == "-" else rest.find(tz)
            frac, zone = rest[:idx], rest[idx:]
        else:
            frac, zone = rest, "+00:00"
        frac = "".join(c for c in frac if c.isdigit())[:6].ljust(6, "0")
        text = f"{head}.{frac}{zone}"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def occupancy_from_runs(runs: list[dict]) -> dict:
    regime = defaultdict(int)
    direction = defaultdict(int)
    trend_dir = defaultdict(int)
    waning = 0
    yearly: dict[str, dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    dir_flips = 0
    prev_dir = None
    same_dir_len = 0
    dir_run_lens: list[int] = []
    for run in runs:
        n = int(run.get("bar_count") or 0)
        if n <= 0:
            continue
        rg = run.get("regime") or "?"
        d = run.get("direction") or "?"
        regime[rg] += n
        direction[d] += n
        if rg == "trend":
            trend_dir[d] += n
        if run.get("trend_waning"):
            waning += n
        if prev_dir is None:
            same_dir_len = n
            prev_dir = d
        elif d == prev_dir:
            same_dir_len += n
        else:
            dir_flips += 1
            dir_run_lens.append(same_dir_len)
            same_dir_len = n
            prev_dir = d
        start = _parse(run["start_time"])
        end = _parse(run["end_time"])
        if start.year == end.year:
            y = yearly[str(start.year)]
            y[rg] += n
            y[f"dir_{d}"] += n
            y["n"] += n
            if rg == "trend":
                y[f"trend_{d}"] += n
        else:
            span = max((end - start).total_seconds(), 1.0)
            cursor = start
            remaining = n
            years = list(range(start.year, end.year + 1))
            weights = []
            for yi, year in enumerate(years):
                if yi == 0:
                    bound = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
                    weights.append(max((min(bound, end) - cursor).total_seconds(), 0.0))
                elif yi == len(years) - 1:
                    weights.append(max((end - datetime(year, 1, 1, tzinfo=timezone.utc)).total_seconds(), 0.0))
                else:
                    a = datetime(year, 1, 1, tzinfo=timezone.utc)
                    b = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
                    weights.append((b - a).total_seconds())
            wsum = sum(weights) or 1.0
            allocated = 0
            for year, w in zip(years, weights):
                part = int(round(n * w / wsum))
                allocated += part
                y = yearly[str(year)]
                y[rg] += part
                y[f"dir_{d}"] += part
                y["n"] += part
                if rg == "trend":
                    y[f"trend_{d}"] += part
            # leftover from rounding
            yearly[str(end.year)]["n"] += n - allocated
    if same_dir_len:
        dir_run_lens.append(same_dir_len)
    total = sum(regime.values()) or 1
    trend_n = regime.get("trend", 0)
    up = direction.get("up", 0)
    down = direction.get("down", 0)
    t_up = trend_dir.get("up", 0)
    t_down = trend_dir.get("down", 0)
    years_out = []
    for y in sorted(yearly):
        rec = yearly[y]
        yn = rec.get("n") or 1
        years_out.append(
            {
                "year": y,
                "n": rec.get("n", 0),
                "trend_share": round(rec.get("trend", 0) / yn, 4),
                "range_share": round(rec.get("range", 0) / yn, 4),
                "mixed_share": round(rec.get("mixed", 0) / yn, 4),
                "dir_up_share": round(rec.get("dir_up", 0) / yn, 4),
                "dir_down_share": round(rec.get("dir_down", 0) / yn, 4),
            }
        )
    return {
        "step_count": total,
        "regime_counts": dict(regime),
        "direction_counts": dict(direction),
        "trend_direction_counts": dict(trend_dir),
        "trend_share": round(trend_n / total, 4),
        "range_share": round(regime.get("range", 0) / total, 4),
        "mixed_share": round(regime.get("mixed", 0) / total, 4),
        "dir_up_share": round(up / total, 4),
        "dir_down_share": round(down / total, 4),
        "dir_imbalance": round(abs(up - down) / total, 4),
        "signed_dir": round((up - down) / total, 4),
        "trend_up_share": round(t_up / trend_n, 4) if trend_n else None,
        "trend_dir_imbalance": round(abs(t_up - t_down) / trend_n, 4) if trend_n else None,
        "waning_share": round(waning / total, 4),
        "direction_flips": dir_flips,
        "mean_direction_run_bars": round(sum(dir_run_lens) / len(dir_run_lens), 2)
        if dir_run_lens
        else None,
        "years": years_out,
    }


async def walk_pair(instrument: str) -> dict:
    dest = OUT / instrument / "regime_ch7.json"
    if dest.exists():
        print(f"SKIP {instrument} occupancy", flush=True)
        return json.loads(dest.read_text())
    existing = EXISTING.get(instrument)
    if existing and existing.exists():
        print(f"REUSE {instrument} from {existing.name}", flush=True)
        raw = json.loads(existing.read_text())
        compact = {
            "instrument": instrument,
            "granularity": "D",
            "from_time": raw.get("from_time"),
            "to_time": raw.get("to_time"),
            "lookback": raw.get("lookback", 250),
            "summary": raw.get("summary"),
            "occupancy": occupancy_from_runs(raw.get("runs") or []),
        }
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(compact, indent=2) + "\n")
        return compact
    print(f"START occupancy {instrument}", flush=True)
    bars = await fetch_walk_bars(instrument, "D", FROM_T, TO_T, 250)
    start_index = regime_walk.first_index_on_or_after(bars, FROM_T)
    result = regime_walk.walk_and_collapse(
        bars,
        lookback=250,
        step=1,
        start_index=start_index,
        horizon=regime_walk.DEFAULT_HORIZON,
        min_n=regime_walk.DEFAULT_MIN_N,
    )
    compact = {
        "instrument": instrument,
        "granularity": "D",
        "from_time": FROM_T,
        "to_time": TO_T,
        "lookback": 250,
        "bar_count": len(bars),
        "start_index": start_index,
        "summary": result["summary"],
        "occupancy": occupancy_from_runs(result["runs"]),
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(compact, indent=2) + "\n")
    print(
        f"DONE {instrument} trend={compact['occupancy']['trend_share']} "
        f"range={compact['occupancy']['range_share']} "
        f"up={compact['occupancy']['dir_up_share']}",
        flush=True,
    )
    return compact


async def main() -> None:
    for instrument in PAIRS:
        try:
            await walk_pair(instrument)
        except (oanda_client.OandaError, regime_walk.WalkError, indicators.IndicatorError) as exc:
            print(f"FAIL {instrument}: {exc}", file=sys.stderr)
            raise


if __name__ == "__main__":
    asyncio.run(main())
