"""USD majors Lien-FX Ch.8 MTF rest-fill paper campaign, H1→M5.

Window: 2026-03-01 … 2026-07-01 (March through June).
M5 sliced at 14 days (OANDA 5000-bar cap). Resume-safe JSON dumps.
Research only; no orders.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from agent.lien_pairs import USD_MAJORS  # noqa: E402
from agent.walk_jobs import compact_walk_payload, execute_walk  # noqa: E402
from app import oanda_client, regime_walk  # noqa: E402

OUT = Path(__file__).resolve().parent
FROM_T = "2026-03-01T00:00:00Z"
TO_T = "2026-07-01T00:00:00Z"
HTF = "H1"
LTF = "M5"
SLICE_DAYS = 14
LOOKBACK = 250
ENTRY_MODE = "peak"
FILL_MODE = "rest"
BALANCE0 = 10_000.0
RISK = 0.02

VPPU: dict[str, float] = {
    "EUR_USD": 1.0,
    "GBP_USD": 1.0,
    "AUD_USD": 1.0,
    "NZD_USD": 1.0,
    "USD_CAD": 1.0 / 1.35,
    "USD_CHF": 1.0 / 0.90,
    "USD_JPY": 1.0 / 150.0,
}
VPPU_NOTE = {
    "USD_CAD": "USD per 1.0 USDCAD move per 1 unit; CAD→USD at 1.35.",
    "USD_CHF": "USD per 1.0 USDCHF move per 1 unit; CHF→USD at 0.90.",
    "USD_JPY": "USD per 1.0 USDJPY move per 1 unit; JPY→USD at 150.",
}


def slices() -> list[tuple[str, str]]:
    start = datetime.fromisoformat(FROM_T.replace("Z", "+00:00"))
    end = datetime.fromisoformat(TO_T.replace("Z", "+00:00"))
    step = timedelta(days=SLICE_DAYS)
    out: list[tuple[str, str]] = []
    t = start
    while t < end:
        nxt = min(t + step, end)
        out.append(
            (
                t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                nxt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        )
        t = nxt
    return out


def pair_dir(instrument: str) -> Path:
    path = OUT / instrument
    path.mkdir(parents=True, exist_ok=True)
    return path


def slim_trades(trades: list[dict]) -> tuple[dict, dict]:
    exits: dict[str, int] = {}
    sides: dict[str, int] = {}
    for t in trades:
        e = t.get("exit_status") or "?"
        s = t.get("side") or "?"
        exits[e] = exits.get(e, 0) + 1
        sides[s] = sides.get(s, 0) + 1
    return sides, exits


def window_key(fr: str, to: str) -> str:
    return f"{fr}|{to}"


def summarize_rows(instrument: str, vppu: float, rows: list[dict]) -> dict:
    r_sum = 0.0
    r_n = 0
    w = l = 0
    mix_exits: dict[str, int] = {}
    have_wl = False
    for r in rows:
        n = int(r.get("trade_count") or 0)
        mr = r.get("mean_r")
        if n and mr is not None:
            r_sum += float(mr) * n
            r_n += n
        if r.get("wins") is not None or r.get("losses") is not None:
            have_wl = True
            w += int(r.get("wins") or 0)
            l += int(r.get("losses") or 0)
        for k, v in (r.get("exits") or {}).items():
            mix_exits[k] = mix_exits.get(k, 0) + int(v)
    if have_wl and r_n and (w + l) < 0.5 * r_n:
        have_wl = False
    if not have_wl:
        w = int(mix_exits.get("target") or 0)
        l = int(mix_exits.get("stop") or 0)
        have_wl = bool(w or l)
        win_from_exits = True
    else:
        win_from_exits = False
    max_dd_frac = 0.0
    peak = BALANCE0
    eq = BALANCE0
    for r in rows:
        eq = float(r["ending_equity"])
        peak = max(peak, eq)
        if peak > 0:
            max_dd_frac = max(max_dd_frac, (peak - eq) / peak)
    summary = {
        "tag": "mtf_h1_m5",
        "instrument": instrument,
        "kind": "mtf",
        "chapter": 8,
        "entry_mode": ENTRY_MODE,
        "fill_mode": FILL_MODE,
        "granularity": HTF,
        "ltf_granularity": LTF,
        "value_per_price_unit": vppu,
        "starting_equity": BALANCE0,
        "ending_equity": round(float(rows[-1]["ending_equity"]) if rows else BALANCE0, 4),
        "slice_count": len(rows),
        "trade_count": r_n,
        "mean_r": round(r_sum / r_n, 4) if r_n else None,
        "sum_r": round(r_sum, 4) if r_n else None,
        "max_drawdown_frac_chained": round(max_dd_frac, 4),
        "exits": mix_exits,
        "from_time": FROM_T,
        "to_time": TO_T,
        "note": (
            f"M5 sliced at {SLICE_DAYS} days (OANDA 5000-bar cap). "
            "window_end at slice edges. Balance chained. "
            "HTF is H1 (not daily); LTF is M5."
        ),
        "slices": rows,
    }
    if vppu_note := VPPU_NOTE.get(instrument):
        summary["value_per_price_unit_note"] = vppu_note
    if have_wl:
        summary["wins"] = w
        summary["losses"] = l
        denom = (w + l) if win_from_exits else r_n
        summary["win_rate"] = round(w / denom, 4) if denom else None
        if win_from_exits:
            summary["win_rate_note"] = "target vs stop; window_end excluded"
    return summary


async def walk_slice(
    instrument: str,
    vppu: float,
    fr: str,
    to: str,
    balance: float,
) -> dict:
    last_err: Exception | None = None
    for attempt in range(1, 5):
        try:
            result, meta = await execute_walk(
                "mtf",
                instrument,
                fr,
                to,
                granularity=HTF,
                ltf_granularity=LTF,
                lookback=LOOKBACK,
                fill_mode=FILL_MODE,
                balance=balance,
                risk_fraction=RISK,
                value_per_price_unit=vppu,
                no_journal=True,
                entry_mode=ENTRY_MODE,
            )
            payload = compact_walk_payload(result, meta, truncate=False)
            eq = payload["equity"]
            trades = payload.get("trades") or []
            sides, exits = slim_trades(trades)
            return {
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
        except (oanda_client.OandaError, regime_walk.WalkError) as exc:
            last_err = exc
            print(
                f"RETRY {instrument} {fr} -> {to} try={attempt} {exc}",
                flush=True,
            )
            await asyncio.sleep(3 * attempt)
    assert last_err is not None
    raise last_err


async def chain(instrument: str, vppu: float) -> dict:
    dest = pair_dir(instrument)
    summary_path = dest / "mtf_summary.json"
    slice_path = dest / "mtf_slices.json"
    windows = slices()
    wanted = {window_key(fr, to) for fr, to in windows}

    if slice_path.exists():
        rows: list[dict] = json.loads(slice_path.read_text())
        rows = [r for r in rows if window_key(r["from_time"], r["to_time"]) in wanted]
    else:
        rows = []

    done = {window_key(r["from_time"], r["to_time"]) for r in rows}
    balance = float(rows[-1]["ending_equity"]) if rows else BALANCE0

    for i, (fr, to) in enumerate(windows):
        key = window_key(fr, to)
        if key in done:
            print(f"SKIP {instrument} {i + 1}/{len(windows)} {fr} -> {to}", flush=True)
            continue
        print(
            f"START {instrument} {i + 1}/{len(windows)} {fr} -> {to} bal={balance:.2f}",
            flush=True,
        )
        row = await walk_slice(instrument, vppu, fr, to, balance)
        row["i"] = i
        rows.append(row)
        rows.sort(key=lambda r: (r["from_time"], r["to_time"]))
        slice_path.write_text(json.dumps(rows, indent=2) + "\n")
        done.add(key)
        balance = float(row["ending_equity"])
        print(
            f"DONE {instrument} {i + 1} n={row['trade_count']} "
            f"mean_r={row['mean_r']} end={balance:.2f} "
            f"ltf={row.get('ltf_bar_count')}",
            flush=True,
        )

    summary = summarize_rows(instrument, vppu, rows)
    summary_path.write_text(json.dumps(summary, indent=2, default=str) + "\n")
    print(
        f"CHAIN DONE {instrument} n={summary['trade_count']} "
        f"end={summary['ending_equity']} mean_r={summary['mean_r']}",
        flush=True,
    )
    return summary


async def main() -> None:
    windows = slices()
    print(
        f"WINDOW {FROM_T} .. {TO_T} {HTF}->{LTF} slices={len(windows)} "
        f"pairs={list(USD_MAJORS)}",
        flush=True,
    )
    pair_summaries: dict[str, dict] = {}
    for instrument in USD_MAJORS:
        vppu = VPPU[instrument]
        slim = await chain(instrument, vppu)
        pair_summaries[instrument] = {
            k: slim[k]
            for k in (
                "ending_equity",
                "trade_count",
                "mean_r",
                "sum_r",
                "max_drawdown_frac_chained",
                "win_rate",
                "wins",
                "losses",
                "exits",
                "value_per_price_unit",
                "slice_count",
            )
            if k in slim
        }

    campaign = {
        "window": f"{FROM_T} to {TO_T}",
        "kind": "mtf",
        "chapter": 8,
        "engine": "mtf",
        "fill_mode": FILL_MODE,
        "mtf_entry_mode": ENTRY_MODE,
        "granularity": HTF,
        "ltf_granularity": LTF,
        "starting_equity": BALANCE0,
        "risk_fraction": RISK,
        "universe": list(USD_MAJORS),
        "slice_days": SLICE_DAYS,
        "pairs": pair_summaries,
        "citation": {
            "source": "lien-fx",
            "chunk_index": 70,
            "claim": (
                "Higher-TF trend first; lower-TF RSI dip/rally with the trend. "
                "Book's common form is D+H1; this campaign shifts the clock to H1+M5 "
                "(closer to the 15-minute RSI example under a higher-TF trend)."
            ),
        },
        "note": (
            "Paper walk, rest fill (next-bar bid/ask). No broker orders. "
            f"M5 sliced at {SLICE_DAYS} days. window_end at slice edges. "
            "H1 is the analysis clock here, not daily and not the trader's macro trend."
        ),
    }
    path = OUT / "campaign.json"
    path.write_text(json.dumps(campaign, indent=2) + "\n")
    print(f"WROTE {path.relative_to(_REPO)}", flush=True)
    for name, row in pair_summaries.items():
        print(
            f"  {name:8} n={row.get('trade_count')}  "
            f"mean_r={row.get('mean_r')}  end={row.get('ending_equity')}  "
            f"dd={row.get('max_drawdown_frac_chained')}",
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(main())
