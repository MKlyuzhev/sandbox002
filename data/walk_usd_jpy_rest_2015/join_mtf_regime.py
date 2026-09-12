"""USD_JPY Ch.8 MTF trades × causal daily Ch.7 regime. Research only."""

from __future__ import annotations

import asyncio
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from agent.walk_jobs import compact_walk_payload, execute_walk  # noqa: E402
from app import regime_walk  # noqa: E402
from app.walk_fetch import fetch_walk_bars  # noqa: E402

OUT = Path(__file__).resolve().parent
SLICES = json.loads((OUT / "mtf_slices.json").read_text())
FROM_T = "2015-01-01T00:00:00Z"
TO_T = "2026-09-10T00:00:00Z"
VPPU = 1.0 / 150.0
TRADES_PATH = OUT / "mtf_trades.json"
STEPS_PATH = OUT / "daily_ch7_steps.json"
JOIN_PATH = OUT / "mtf_r_by_regime.json"


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return round(num / (dx * dy), 4)


def bucket_stats(rows: list[dict], key: str = "r") -> dict:
    rs = [float(r[key]) for r in rows if r.get(key) is not None]
    n = len(rs)
    if not n:
        return {"n": 0}
    wins = sum(1 for x in rs if x > 0)
    stops = sum(1 for r in rows if r.get("exit_status") == "stop")
    targets = sum(1 for r in rows if r.get("exit_status") == "target")
    we = sum(1 for r in rows if r.get("exit_status") == "window_end")
    return {
        "n": n,
        "mean_r": round(sum(rs) / n, 4),
        "win_rate": round(wins / n, 4),
        "sum_r": round(sum(rs), 4),
        "stops": stops,
        "targets": targets,
        "window_end": we,
    }


def last_step_at(steps: list[dict], entry_time: str) -> dict | None:
    t = regime_walk._parse_rfc3339_utc(entry_time)  # noqa: SLF001
    if t is None:
        return None
    hit = None
    for step in steps:
        ts = regime_walk._parse_rfc3339_utc(step.get("time"))  # noqa: SLF001
        if ts is not None and ts <= t:
            hit = step
        elif ts is not None and ts > t:
            break
    return hit


async def load_steps() -> list[dict]:
    if STEPS_PATH.exists():
        print("SKIP daily steps", flush=True)
        return json.loads(STEPS_PATH.read_text())
    print("START daily Ch.7 steps", flush=True)
    bars = await fetch_walk_bars("USD_JPY", "D", FROM_T, TO_T, 250)
    start_index = regime_walk.first_index_on_or_after(bars, FROM_T)
    raw = regime_walk.walk(bars, lookback=250, step=1, start_index=start_index)
    compact = [
        {
            "time": s.get("time"),
            "regime": s.get("regime"),
            "direction": s.get("direction"),
            "trend_waning": bool(s.get("trend_waning")),
        }
        for s in raw
    ]
    STEPS_PATH.write_text(json.dumps(compact) + "\n")
    print(f"DONE daily steps n={len(compact)}", flush=True)
    return compact


async def load_trades() -> list[dict]:
    rows: list[dict] = []
    if TRADES_PATH.exists():
        rows = json.loads(TRADES_PATH.read_text())
    done = {r["slice_i"] for r in rows}
    balance = 10_000.0
    if rows:
        last = max(rows, key=lambda r: r["slice_i"])
        # ending equity of last completed slice from slices file
        for sl in SLICES:
            if sl["i"] == last["slice_i"]:
                balance = float(sl["ending_equity"])
                break
    for sl in SLICES:
        i = int(sl["i"])
        if i in done:
            print(f"SKIP mtf trades slice {i + 1}/24", flush=True)
            continue
        start_eq = float(sl["starting_equity"])
        print(
            f"START mtf trades {i + 1}/24 {sl['from_time']} bal={start_eq:.2f}",
            flush=True,
        )
        result, meta = await execute_walk(
            "mtf",
            "USD_JPY",
            sl["from_time"],
            sl["to_time"],
            granularity="D",
            ltf_granularity="H1",
            lookback=250,
            fill_mode="rest",
            balance=start_eq,
            risk_fraction=0.02,
            value_per_price_unit=VPPU,
            no_journal=True,
            entry_mode="peak",
        )
        payload = compact_walk_payload(result, meta, truncate=False)
        for t in payload.get("trades") or []:
            rows.append(
                {
                    "slice_i": i,
                    "entry_time": t.get("entry_time"),
                    "exit_time": t.get("exit_time"),
                    "side": t.get("side"),
                    "exit_status": t.get("exit_status"),
                    "r": t.get("r_realized"),
                    "entry": t.get("entry"),
                    "stop": t.get("stop"),
                    "target": t.get("target"),
                    "pnl": t.get("pnl"),
                }
            )
        TRADES_PATH.write_text(json.dumps(rows, indent=2) + "\n")
        eq = payload["equity"]
        print(
            f"DONE mtf trades {i + 1} n={payload['trade_count']} "
            f"mean_r={eq.get('mean_r')}",
            flush=True,
        )
        done.add(i)
    return rows


def analyze(trades: list[dict], steps: list[dict]) -> dict:
    joined = []
    missing = 0
    for t in trades:
        r = t.get("r")
        if r is None:
            continue
        step = last_step_at(steps, t["entry_time"])
        if step is None:
            missing += 1
            continue
        regime = step.get("regime") or "unknown"
        direction = step.get("direction")
        waning = bool(step.get("trend_waning"))
        side = t.get("side")
        aligned = None
        if direction == "up" and side == "long":
            aligned = True
        elif direction == "down" and side == "short":
            aligned = True
        elif direction in ("up", "down") and side in ("long", "short"):
            aligned = False
        joined.append(
            {
                **t,
                "regime": regime,
                "direction": direction,
                "trend_waning": waning,
                "aligned": aligned,
                "year": str(t.get("entry_time") or "")[:4],
            }
        )

    by_reg: dict[str, list] = defaultdict(list)
    for row in joined:
        by_reg[row["regime"]].append(row)

    rs = [float(x["r"]) for x in joined]
    is_trend = [1.0 if x["regime"] == "trend" else 0.0 for x in joined]
    is_range = [1.0 if x["regime"] == "range" else 0.0 for x in joined]
    is_mixed = [1.0 if x["regime"] == "mixed" else 0.0 for x in joined]
    is_waning = [1.0 if x["trend_waning"] else 0.0 for x in joined]
    is_aligned = [
        1.0 if x["aligned"] else 0.0 for x in joined if x["aligned"] is not None
    ]
    r_aligned = [float(x["r"]) for x in joined if x["aligned"] is not None]

    # trend vs not-trend, trend vs range only
    trend_range = [x for x in joined if x["regime"] in ("trend", "range")]
    r_tr = [float(x["r"]) for x in trend_range]
    dummy_tr = [1.0 if x["regime"] == "trend" else 0.0 for x in trend_range]

    yearly = []
    by_year: dict[str, list] = defaultdict(list)
    for row in joined:
        by_year[row["year"]].append(row)
    for y in sorted(by_year):
        rec = {"year": y, "all": bucket_stats(by_year[y])}
        for rg in ("trend", "range", "mixed"):
            rec[rg] = bucket_stats([x for x in by_year[y] if x["regime"] == rg])
        yearly.append(rec)

    aligned_rows = [x for x in joined if x["aligned"] is True]
    contra_rows = [x for x in joined if x["aligned"] is False]
    waning_rows = [x for x in joined if x["trend_waning"]]
    not_waning = [x for x in joined if not x["trend_waning"]]

    # regime × aligned
    cross = {}
    for rg in ("trend", "range", "mixed"):
        for flag, name in ((True, "aligned"), (False, "contra")):
            cross[f"{rg}_{name}"] = bucket_stats(
                [x for x in joined if x["regime"] == rg and x["aligned"] is flag]
            )

    out = {
        "instrument": "USD_JPY",
        "engine": "mtf",
        "chapter": 8,
        "fill_mode": "rest",
        "n_trades": len(joined),
        "n_unjoined": missing,
        "overall": bucket_stats(joined),
        "by_regime": {k: bucket_stats(v) for k, v in sorted(by_reg.items())},
        "by_waning": {
            "waning": bucket_stats(waning_rows),
            "not_waning": bucket_stats(not_waning),
        },
        "by_alignment": {
            "htf_aligned": bucket_stats(aligned_rows),
            "htf_contra": bucket_stats(contra_rows),
        },
        "regime_x_alignment": cross,
        "correlations": {
            "r_vs_is_trend": pearson(rs, is_trend),
            "r_vs_is_range": pearson(rs, is_range),
            "r_vs_is_mixed": pearson(rs, is_mixed),
            "r_vs_is_trend_excluding_mixed": pearson(r_tr, dummy_tr),
            "r_vs_waning": pearson(rs, is_waning),
            "r_vs_htf_aligned": pearson(r_aligned, is_aligned),
            "n": len(rs),
            "n_trend_vs_range": len(r_tr),
            "note": (
                "Pearson of realized R with a 0/1 dummy. Equivalent to "
                "point-biserial. R is bimodal near -1 and +2, so r is modest "
                "even when mean R differs."
            ),
        },
        "yearly": yearly,
        "join_rule": (
            "Last complete daily Ch.7 step with time <= trade entry_time "
            "(same causal rule as mtf_walk.htf_index_as_of)."
        ),
    }
    JOIN_PATH.write_text(json.dumps(out, indent=2) + "\n")
    return out


async def main() -> None:
    steps = await load_steps()
    trades = await load_trades()
    report = analyze(trades, steps)
    print(json.dumps({k: report[k] for k in report if k != "yearly"}, indent=2))
    print("years", len(report["yearly"]))


if __name__ == "__main__":
    asyncio.run(main())
