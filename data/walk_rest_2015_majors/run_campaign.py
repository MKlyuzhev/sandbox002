"""USD majors rest-fill paper campaign (no Ch.7). Resume-safe JSON dumps."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from agent.walk_jobs import compact_walk_payload, execute_walk  # noqa: E402

OUT = Path(__file__).resolve().parent
FROM_T = "2015-01-01T00:00:00Z"
TO_T = "2026-09-10T00:00:00Z"
PAIRS = {
    "GBP_USD": 1.0,
    "EUR_USD": 1.0,
    "AUD_USD": 1.0,
    "USD_CHF": 1.0 / 0.90,
    "EUR_JPY": 1.0 / 150.0,
}


def slices() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for y in range(2015, 2027):
        a = f"{y}-01-01T00:00:00Z"
        b = f"{y}-07-01T00:00:00Z"
        out.append((a, b))
        a2 = b
        b2 = TO_T if y == 2026 else f"{y + 1}-01-01T00:00:00Z"
        out.append((a2, b2))
    return out


def slim_trades(trades: list[dict]) -> tuple[dict, dict]:
    exits: dict[str, int] = {}
    sides: dict[str, int] = {}
    for t in trades:
        e = t.get("exit_status") or "?"
        s = t.get("side") or "?"
        exits[e] = exits.get(e, 0) + 1
        sides[s] = sides.get(s, 0) + 1
    return sides, exits


def pair_dir(instrument: str) -> Path:
    path = OUT / instrument
    path.mkdir(parents=True, exist_ok=True)
    return path


async def run_daily(instrument: str, vppu: float) -> None:
    dest = pair_dir(instrument)
    for chapter in (9, 14, 16):
        path = dest / f"daily_ch{chapter}.json"
        if path.exists():
            print(f"SKIP daily {instrument} ch{chapter}", flush=True)
            continue
        print(f"START daily {instrument} ch{chapter}", flush=True)
        result, meta = await execute_walk(
            "lien",
            instrument,
            FROM_T,
            TO_T,
            chapter=chapter,
            granularity="D",
            lookback=250,
            fill_mode="rest",
            balance=10_000.0,
            risk_fraction=0.02,
            value_per_price_unit=vppu,
            no_journal=True,
        )
        payload = compact_walk_payload(result, meta, truncate=False)
        path.write_text(json.dumps(payload, indent=2, default=str) + "\n")
        eq = payload["equity"]
        print(
            f"DONE daily {instrument} ch{chapter} n={payload['trade_count']} "
            f"mean_r={eq.get('mean_r')} end={eq.get('ending_equity')}",
            flush=True,
        )


async def chain(instrument: str, vppu: float, kind: str, extra: dict, tag: str) -> None:
    dest = pair_dir(instrument)
    summary_path = dest / f"{tag}_summary.json"
    slice_path = dest / f"{tag}_slices.json"
    rows: list[dict] = []
    if slice_path.exists():
        rows = json.loads(slice_path.read_text())
    done = {r["from_time"] + "|" + r["to_time"] for r in rows}
    balance = float(rows[-1]["ending_equity"]) if rows else 10_000.0
    peak = max([10_000.0] + [float(r["ending_equity"]) for r in rows])
    all_r: list[float] = []
    wins = losses = scratches = 0
    for r in rows:
        # R totals rebuilt at the end from slice files if present; keep running
        pass
    for i, (fr, to) in enumerate(slices()):
        key = f"{fr}|{to}"
        if key in done:
            print(f"SKIP {tag} {instrument} {i + 1}/24", flush=True)
            continue
        print(
            f"START {tag} {instrument} {i + 1}/24 {fr} -> {to} bal={balance:.2f}",
            flush=True,
        )
        result, meta = await execute_walk(
            kind,
            instrument,
            fr,
            to,
            granularity="D",
            ltf_granularity="H1",
            lookback=250,
            fill_mode="rest",
            balance=balance,
            risk_fraction=0.02,
            value_per_price_unit=vppu,
            no_journal=True,
            **extra,
        )
        payload = compact_walk_payload(result, meta, truncate=False)
        eq = payload["equity"]
        ending = float(eq["ending_equity"])
        trades = payload.get("trades") or []
        sides, exits = slim_trades(trades)
        row = {
            "i": i,
            "from_time": fr,
            "to_time": to,
            "walk_id": payload.get("walk_id"),
            "starting_equity": eq.get("starting_equity"),
            "ending_equity": ending,
            "trade_count": payload.get("trade_count"),
            "wins": eq.get("wins"),
            "losses": eq.get("losses"),
            "mean_r": eq.get("mean_r"),
            "win_rate": eq.get("win_rate"),
            "max_drawdown_frac": eq.get("max_drawdown_frac"),
            "sides": sides,
            "exits": exits,
            "ltf_bar_count": payload.get("ltf_bar_count"),
            "htf_bar_count": payload.get("htf_bar_count"),
        }
        rows.append(row)
        slice_path.write_text(json.dumps(rows, indent=2) + "\n")
        balance = ending
        peak = max(peak, ending)
        print(
            f"DONE {tag} {instrument} {i + 1} n={row['trade_count']} "
            f"mean_r={row['mean_r']} end={ending:.2f}",
            flush=True,
        )

    for r in rows:
        # mean_r * n is not exact; recompute from daily dumps if needed later
        pass
    r_sum = 0.0
    r_n = 0
    w = l = 0
    mix_exits: dict[str, int] = {}
    for r in rows:
        n = int(r.get("trade_count") or 0)
        mr = r.get("mean_r")
        if n and mr is not None:
            r_sum += float(mr) * n
            r_n += n
        w += int(r.get("wins") or 0)
        l += int(r.get("losses") or 0)
        for k, v in (r.get("exits") or {}).items():
            mix_exits[k] = mix_exits.get(k, 0) + int(v)
    max_dd_frac = 0.0
    peak = 10_000.0
    eq = 10_000.0
    for r in rows:
        eq = float(r["ending_equity"])
        peak = max(peak, eq)
        if peak > 0:
            max_dd_frac = max(max_dd_frac, (peak - eq) / peak)
    summary = {
        "tag": tag,
        "instrument": instrument,
        "kind": kind,
        **extra,
        "fill_mode": "rest",
        "value_per_price_unit": vppu,
        "starting_equity": 10_000.0,
        "ending_equity": round(float(rows[-1]["ending_equity"]) if rows else 10_000.0, 4),
        "slice_count": len(rows),
        "trade_count": r_n,
        "wins": w,
        "losses": l,
        "win_rate": round(w / r_n, 4) if r_n else None,
        "mean_r": round(r_sum / r_n, 4) if r_n else None,
        "sum_r": round(r_sum, 4) if r_n else None,
        "max_drawdown_frac_chained": round(max_dd_frac, 4),
        "exits": mix_exits,
        "note": "H1 sliced at 6 months (OANDA 5000-bar cap). window_end at slice edges. Balance chained.",
        "slices": rows,
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    print(
        f"CHAIN DONE {tag} {instrument} n={summary['trade_count']} "
        f"end={summary['ending_equity']} mean_r={summary['mean_r']}",
        flush=True,
    )


async def main() -> None:
    for instrument, vppu in PAIRS.items():
        await run_daily(instrument, vppu)
        await chain(instrument, vppu, "mtf", {"entry_mode": "peak"}, "mtf")
        await chain(instrument, vppu, "lien", {"chapter": 13}, "fader")


if __name__ == "__main__":
    asyncio.run(main())
