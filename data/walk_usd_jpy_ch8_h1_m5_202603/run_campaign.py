"""USD_JPY Lien-FX Ch.8 MTF rest-fill walk: H1 bias, M5 entry, Mar–Jun 2026.

Two-week slices keep M5 under the OANDA 5000-bar cap. Research only; no orders.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from agent.walk_jobs import compact_walk_payload, execute_walk  # noqa: E402
from app import oanda_client, regime_walk  # noqa: E402

OUT = Path(__file__).resolve().parent
INSTRUMENT = "USD_JPY"
FROM_T = "2026-03-01T00:00:00Z"
TO_T = "2026-07-01T00:00:00Z"
WINDOWS = [
    ("2026-03-01T00:00:00Z", "2026-03-15T00:00:00Z"),
    ("2026-03-15T00:00:00Z", "2026-04-01T00:00:00Z"),
    ("2026-04-01T00:00:00Z", "2026-04-15T00:00:00Z"),
    ("2026-04-15T00:00:00Z", "2026-05-01T00:00:00Z"),
    ("2026-05-01T00:00:00Z", "2026-05-15T00:00:00Z"),
    ("2026-05-15T00:00:00Z", "2026-06-01T00:00:00Z"),
    ("2026-06-01T00:00:00Z", "2026-06-15T00:00:00Z"),
    ("2026-06-15T00:00:00Z", "2026-07-01T00:00:00Z"),
]
LOOKBACK = 250
ENTRY_MODE = "peak"
FILL_MODE = "rest"
BALANCE0 = 10_000.0
RISK = 0.02
VPPU = 1.0 / 150.0
HTF = "H1"
LTF = "M5"


def slim_trades(trades: list[dict]) -> tuple[dict, dict]:
    exits: dict[str, int] = {}
    sides: dict[str, int] = {}
    for t in trades:
        e = t.get("exit_status") or "?"
        s = t.get("side") or "?"
        exits[e] = exits.get(e, 0) + 1
        sides[s] = sides.get(s, 0) + 1
    return sides, exits


def summarize(rows: list[dict], all_trades: list[dict]) -> dict:
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
    peak = BALANCE0
    eq = BALANCE0
    for r in rows:
        eq = float(r["ending_equity"])
        peak = max(peak, eq)
        if peak > 0:
            max_dd_frac = max(max_dd_frac, (peak - eq) / peak)
    return {
        "instrument": INSTRUMENT,
        "kind": "mtf",
        "chapter": 8,
        "granularity": HTF,
        "ltf_granularity": LTF,
        "entry_mode": ENTRY_MODE,
        "fill_mode": FILL_MODE,
        "from_time": FROM_T,
        "to_time": TO_T,
        "lookback": LOOKBACK,
        "starting_equity": BALANCE0,
        "risk_fraction": RISK,
        "value_per_price_unit": VPPU,
        "value_per_price_unit_note": (
            "USD per 1.0 USDJPY move per 1 unit; JPY→USD at 150. "
            "Scales cash P&L and size, not R."
        ),
        "ending_equity": round(float(rows[-1]["ending_equity"]) if rows else BALANCE0, 4),
        "slice_count": len(rows),
        "trade_count": r_n,
        "wins": w,
        "losses": l,
        "win_rate": round(w / r_n, 4) if r_n else None,
        "mean_r": round(r_sum / r_n, 4) if r_n else None,
        "sum_r": round(r_sum, 4) if r_n else None,
        "max_drawdown_frac_chained": round(max_dd_frac, 4),
        "exits": mix_exits,
        "citation": {
            "source": "lien-fx",
            "chunk_index": 70,
            "claim": (
                "Higher TF sets direction; lower TF times RSI dip/rally with "
                "the trend. Book figure is M15; this walk uses M5 under H1."
            ),
        },
        "note": (
            "Two-week M5 slices (OANDA 5000-bar cap). window_end at slice "
            "edges. Balance chained. Paper rest fill; no broker orders."
        ),
        "slices": rows,
        "trades": all_trades,
    }


async def walk_slice(fr: str, to: str, balance: float) -> tuple[dict, list[dict]]:
    last_err: Exception | None = None
    for attempt in range(1, 5):
        try:
            result, meta = await execute_walk(
                "mtf",
                INSTRUMENT,
                fr,
                to,
                granularity=HTF,
                ltf_granularity=LTF,
                lookback=LOOKBACK,
                fill_mode=FILL_MODE,
                balance=balance,
                risk_fraction=RISK,
                value_per_price_unit=VPPU,
                no_journal=True,
                entry_mode=ENTRY_MODE,
            )
            payload = compact_walk_payload(result, meta, truncate=False)
            eq = payload["equity"]
            trades = payload.get("trades") or []
            sides, exits = slim_trades(trades)
            row = {
                "from_time": fr,
                "to_time": to,
                "walk_id": payload.get("walk_id"),
                "starting_equity": eq.get("starting_equity"),
                "ending_equity": float(eq["ending_equity"]),
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
            return row, trades
        except (oanda_client.OandaError, regime_walk.WalkError) as exc:
            last_err = exc
            print(f"RETRY {fr} -> {to} try={attempt} {exc}", flush=True)
            await asyncio.sleep(3 * attempt)
    assert last_err is not None
    raise last_err


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    slice_path = OUT / "slices.json"
    rows: list[dict] = []
    if slice_path.exists():
        rows = json.loads(slice_path.read_text())
    done = {f"{r['from_time']}|{r['to_time']}" for r in rows}
    balance = float(rows[-1]["ending_equity"]) if rows else BALANCE0
    all_trades: list[dict] = []
    trades_path = OUT / "trades.json"
    if trades_path.exists() and rows:
        all_trades = json.loads(trades_path.read_text())

    print(
        f"WINDOW {FROM_T} .. {TO_T} {INSTRUMENT} {HTF}+{LTF} "
        f"fill={FILL_MODE} entry={ENTRY_MODE}",
        flush=True,
    )
    for i, (fr, to) in enumerate(WINDOWS):
        key = f"{fr}|{to}"
        if key in done:
            print(f"SKIP {i + 1}/{len(WINDOWS)} {fr} -> {to}", flush=True)
            continue
        print(
            f"START {i + 1}/{len(WINDOWS)} {fr} -> {to} bal={balance:.2f}",
            flush=True,
        )
        row, trades = await walk_slice(fr, to, balance)
        row["i"] = i
        for t in trades:
            t["slice"] = i
        rows.append(row)
        all_trades.extend(trades)
        slice_path.write_text(json.dumps(rows, indent=2) + "\n")
        trades_path.write_text(json.dumps(all_trades, indent=2, default=str) + "\n")
        balance = float(row["ending_equity"])
        print(
            f"DONE {i + 1} n={row['trade_count']} mean_r={row['mean_r']} "
            f"end={balance:.2f} ltf={row['ltf_bar_count']}",
            flush=True,
        )

    summary = summarize(rows, all_trades)
    path = OUT / "campaign.json"
    path.write_text(json.dumps(summary, indent=2, default=str) + "\n")
    print(
        f"CHAIN DONE n={summary['trade_count']} end={summary['ending_equity']} "
        f"mean_r={summary['mean_r']} wr={summary['win_rate']}",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
