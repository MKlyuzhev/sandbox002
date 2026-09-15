"""Causal Ch.10 double-zeros campaign: USD_JPY M15, 2015-now.

Research only; no orders. Fetches OANDA D + M15 once (cached), walks with a
cached daily Ch.7 fade_range gate. Fill at decision-bar close; 2% equity;
vppu=1.0. News/NFP/FOMC is not filtered.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent
_S002 = OUT.parents[1]
if str(_S002) not in sys.path:
    sys.path.insert(0, str(_S002))

from agent.double_zeros_walk import _proposal_from_out  # noqa: E402
from agent.engines import double_zeros as zeros_mod  # noqa: E402
from agent.paper_walk import (  # noqa: E402
    _close_trade,
    _goal_for_walk,
    check_exit,
    summarize_equity,
)
from agent.schema import Goal, PaperTrade  # noqa: E402
from agent.walk_exec import fill_mode_of, may_check_exit, window_end_price  # noqa: E402
from agent import policy  # noqa: E402
from app import oanda_client, regime as regime_mod, regime_walk  # noqa: E402

FROM_T = "2015-01-01T00:00:00Z"
TO_T = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
D_FROM = "2014-01-01T00:00:00Z"
INSTRUMENT = "USD_JPY"
LOOKBACK = 250
LTF_TAIL = 48  # SMA-20 + a few extra M15 bars
START_EQUITY = 10_000.0
RISK_FRAC = 0.02
VPPU = 1.0

D_CACHE = OUT / "usd_jpy_d.json"
M15_CACHE = OUT / "usd_jpy_m15.json"
TRADES_PATH = OUT / "double_zeros_trades.json"
SUMMARY_PATH = OUT / "double_zeros_campaign.json"


def _compact(bar: dict) -> dict:
    return {
        "time": bar["time"],
        "open": bar["open"],
        "high": bar["high"],
        "low": bar["low"],
        "close": bar["close"],
        "complete": True,
    }


async def load_ohlc(granularity: str, from_time: str, cache: Path) -> list[dict]:
    if cache.exists():
        bars = json.loads(cache.read_text())
        print(f"CACHE {cache.name} n={len(bars)} last={bars[-1]['time']}", flush=True)
        return bars
    print(f"FETCH {INSTRUMENT} {granularity} {from_time} .. {TO_T}", flush=True)
    payload = await oanda_client.get_candles(
        INSTRUMENT,
        granularity=granularity,
        count=None,
        price="M",
        from_time=from_time,
        to_time=TO_T,
    )
    bars = [
        _compact(b)
        for b in oanda_client.candles_to_bars(payload, prefer="mid")
        if b.get("complete", True)
    ]
    cache.write_text(json.dumps(bars))
    print(f"WROTE {cache.name} n={len(bars)}", flush=True)
    return bars


def year_of(ts: str) -> str:
    return (ts or "")[:4]


def build_htf_cache(htf_bars: list[dict]) -> dict[str, dict]:
    cache: dict[str, dict] = {}
    series = regime_walk.drop_incomplete(htf_bars)
    n = len(series)
    print(f"HTF classify n={n} lookback={LOOKBACK}", flush=True)
    for i in range(LOOKBACK - 1, n):
        window = series[: i + 1][-LOOKBACK:]
        t = str(series[i].get("time") or "")
        analysis = dict(regime_mod.analyze_bars(window))
        analysis.setdefault("instrument", INSTRUMENT)
        analysis.setdefault("granularity", "D")
        cache[t] = analysis
        if (i - LOOKBACK + 1) % 500 == 0:
            print(f"  htf {i + 1}/{n} {t}", flush=True)
    print(f"HTF cache {len(cache)}", flush=True)
    return cache


def htf_as_of(cache: dict[str, dict], times: list[str], t: str) -> dict | None:
    import bisect

    i = bisect.bisect_right(times, t) - 1
    if i < 0:
        return None
    return cache.get(times[i])


def walk_campaign(
    htf_bars: list[dict],
    ltf_bars: list[dict],
    htf_cache: dict[str, dict],
) -> tuple[list[PaperTrade], dict]:
    htf_series = regime_walk.drop_incomplete(htf_bars)
    ltf_series = regime_walk.drop_incomplete(ltf_bars)
    start_index = regime_walk.first_index_on_or_after(ltf_series, FROM_T)
    htf_times = sorted(htf_cache)
    goal = _goal_for_walk(
        Goal(
            instrument=INSTRUMENT,
            granularity="D",
            ltf_granularity="M15",
            mode="paper",
            from_time=FROM_T,
            to_time=TO_T,
            risk_fraction=RISK_FRAC,
            balance=START_EQUITY,
            value_per_price_unit=VPPU,
            fill_mode="close",
            no_rag=True,
            no_llm=True,
        )
    )
    walk_id = uuid.uuid4().hex
    equity = START_EQUITY
    trades: list[PaperTrade] = []
    open_trade: PaperTrade | None = None
    prev_fired = False
    last_i = len(ltf_series) - 1
    fill_mode = fill_mode_of(goal)
    n_eval = 0
    n_fire = 0
    n_policy_block = 0
    n_fade_range = 0
    n_triple = 0
    play_counts: Counter[str] = Counter()

    print(
        f"WALK M15 start_index={start_index}/{len(ltf_series)} "
        f"from={ltf_series[start_index].get('time')}",
        flush=True,
    )
    for i in range(start_index, len(ltf_series)):
        bar = ltf_series[i]
        bar_time = str(bar.get("time") or "")
        if i % 20000 == 0:
            print(f"  ltf {i}/{last_i} {bar_time} trades={len(trades)}", flush=True)

        exited_here = False
        if open_trade is not None and may_check_exit(
            open_trade.entry_index, i, fill_mode
        ):
            hit = check_exit(
                open_trade.side, open_trade.stop, open_trade.target, bar
            )
            if hit is not None:
                status, price = hit
                open_trade, equity = _close_trade(
                    open_trade,
                    exit_index=i,
                    exit_time=bar_time,
                    exit_price=price,
                    exit_status=status,
                    journal=None,
                    equity=equity,
                    risk_fraction=goal.risk_fraction,
                    fill_mode=fill_mode,
                    value_per_price_unit=goal.value_per_price_unit,
                )
                trades.append(open_trade)
                open_trade = None
                exited_here = True

        if open_trade is None and not exited_here:
            htf_analysis = htf_as_of(htf_cache, htf_times, bar_time)
            fired = False
            out = None
            ticket = None
            if htf_analysis is not None:
                n_eval += 1
                if "fade_range" in set(htf_analysis.get("allowed_play_classes") or []):
                    n_fade_range += 1
                ltf_window = ltf_series[max(0, i + 1 - LTF_TAIL) : i + 1]
                out = zeros_mod.double_zeros_signal(
                    htf_analysis,
                    ltf_window,
                    INSTRUMENT,
                    htf_granularity="D",
                    ltf_granularity="M15",
                )
                ticket = out.get("ticket")
                fired = bool(
                    out and out["signal"] in ("long", "short") and ticket
                )
            if fired and not prev_fired:
                assert out is not None and ticket is not None and htf_analysis is not None
                n_fire += 1
                setup = out.get("setup") or {}
                if setup.get("triple_zero"):
                    n_triple += 1
                proposal = _proposal_from_out(out, ticket, bar_time)
                verdict = policy.evaluate(htf_analysis, proposal, goal)
                if verdict.ok:
                    play_counts[str(proposal.play_class)] += 1
                    run_id = uuid.uuid4().hex
                    open_trade = PaperTrade(
                        run_id=run_id,
                        entry_index=i,
                        entry_time=bar_time,
                        side=proposal.side,  # type: ignore[arg-type]
                        play_class=proposal.play_class,
                        entry=float(ticket["entry"]),
                        stop=float(ticket["stop"]),
                        target=float(ticket["target"]),
                        reasons=list(verdict.reasons),
                        walk_id=walk_id,
                    )
                else:
                    n_policy_block += 1
            prev_fired = fired

    if open_trade is not None:
        last = ltf_series[last_i]
        open_trade, equity = _close_trade(
            open_trade,
            exit_index=last_i,
            exit_time=str(last.get("time") or ""),
            exit_price=window_end_price(last, open_trade.side, fill_mode),
            exit_status="window_end",
            journal=None,
            equity=equity,
            risk_fraction=goal.risk_fraction,
            fill_mode=fill_mode,
            value_per_price_unit=goal.value_per_price_unit,
        )
        trades.append(open_trade)

    meta = {
        "walk_id": walk_id,
        "start_index": start_index,
        "ltf_bar_count": len(ltf_series),
        "htf_bar_count": len(htf_series),
        "n_eval": n_eval,
        "n_fade_range": n_fade_range,
        "n_fire": n_fire,
        "n_triple_zero": n_triple,
        "n_policy_block": n_policy_block,
        "play_counts": dict(play_counts),
    }
    return trades, meta


def year_rows(trades: list[PaperTrade], starting: float) -> list[dict]:
    by: dict[str, list[PaperTrade]] = defaultdict(list)
    for t in trades:
        by[year_of(t.entry_time)].append(t)
    rows = []
    eq = starting
    for y in sorted(by):
        chunk = by[y]
        rs = [float(t.r_realized) for t in chunk if t.r_realized is not None]
        wins = sum(1 for r in rs if r > 0)
        start_eq = eq
        if chunk and chunk[-1].equity_after is not None:
            eq = float(chunk[-1].equity_after)
        sides = Counter(t.side for t in chunk)
        exits = Counter(t.exit_status for t in chunk)
        rows.append(
            {
                "year": y,
                "n": len(chunk),
                "wins": wins,
                "win_rate": round(wins / len(rs), 4) if rs else None,
                "mean_r": round(sum(rs) / len(rs), 4) if rs else None,
                "sum_r": round(sum(rs), 4) if rs else None,
                "starting_equity": round(start_eq, 2),
                "ending_equity": round(eq, 2),
                "long": sides.get("long", 0),
                "short": sides.get("short", 0),
                "exits": dict(exits),
            }
        )
    return rows


def side_block(trades: list[PaperTrade], side: str) -> dict:
    chunk = [t for t in trades if t.side == side]
    rs = [float(t.r_realized) for t in chunk if t.r_realized is not None]
    n = len(rs)
    return {
        "n": len(chunk),
        "mean_r": round(sum(rs) / n, 4) if n else None,
        "win_rate": round(sum(1 for r in rs if r > 0) / n, 4) if n else None,
        "sum_r": round(sum(rs), 4) if n else None,
    }


async def main() -> int:
    d_bars = await load_ohlc("D", D_FROM, D_CACHE)
    m15_bars = await load_ohlc("M15", FROM_T, M15_CACHE)
    htf_cache = build_htf_cache(d_bars)
    trades, meta = walk_campaign(d_bars, m15_bars, htf_cache)
    equity = summarize_equity(meta["walk_id"], trades, START_EQUITY, RISK_FRAC)

    dump = [t.model_dump(mode="json") for t in trades]
    TRADES_PATH.write_text(json.dumps(dump, indent=2))
    holds = []
    for t in trades:
        if t.entry_time and t.exit_time:
            a = regime_walk._parse_rfc3339_utc(t.entry_time)
            b = regime_walk._parse_rfc3339_utc(t.exit_time)
            if a is not None and b is not None:
                holds.append((b - a) / 3600.0)
    summary = {
        "instrument": INSTRUMENT,
        "engine": "double_zeros",
        "chapter": 10,
        "fill_mode": "close",
        "note": (
            "Causal Ch.10 paper walk. Daily Ch.7 fade_range gate; M15 20-SMA "
            "and 10–15 pip band off a 100-pip figure; stop 20 pips beyond. "
            "2R tickets, 2% equity. News/NFP/FOMC not filtered. Not orders."
        ),
        "citations": [
            {"source": "lien-fx", "chunk_index": 77},
            {"source": "lien-fx", "chunk_index": 78},
            {"source": "lien-fx", "chunk_index": 79},
        ],
        "window": f"{FROM_T} to {TO_T}",
        "last_m15": m15_bars[-1]["time"] if m15_bars else None,
        "last_daily": d_bars[-1]["time"] if d_bars else None,
        "value_per_price_unit": VPPU,
        "starting_equity": START_EQUITY,
        "risk_fraction": RISK_FRAC,
        "lookback": LOOKBACK,
        "equity": equity.model_dump(mode="json"),
        "trade_count": len(trades),
        "exits": dict(Counter(t.exit_status for t in trades)),
        "by_side": {
            "long": side_block(trades, "long"),
            "short": side_block(trades, "short"),
        },
        "by_play_class": dict(Counter(t.play_class for t in trades)),
        "mean_hold_hours": round(sum(holds) / len(holds), 2) if holds else None,
        "median_hold_hours": (
            round(sorted(holds)[len(holds) // 2], 2) if holds else None
        ),
        "years": year_rows(trades, START_EQUITY),
        **meta,
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, default=str))
    print(
        json.dumps(
            {
                k: summary[k]
                for k in (
                    "instrument",
                    "trade_count",
                    "equity",
                    "exits",
                    "by_side",
                    "n_fire",
                    "n_triple_zero",
                    "n_fade_range",
                )
            },
            indent=2,
            default=str,
        ),
        flush=True,
    )
    print(f"WROTE {SUMMARY_PATH}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
