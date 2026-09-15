"""Causal Ch.11 waiting_deal rest-fill campaign: four GBP pairs, 2015-now.

Research only; no orders. Fetches OANDA D (mid) + M15 MBA once per pair
(cached). Daily Ch.7 gate cached. Fill at next-bar taking-side open
(long ask / short bid); exit on making side. 2% equity; vppu=1.0.
Hunt remains 25 pips (book GBPUSD number, not retuned). News/FOMC not
filtered. Clock is DST-aware Frankfurt→London.
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

from agent.engines import waiting_deal as deal_mod  # noqa: E402
from agent.levels import pip_size  # noqa: E402
from agent.paper_walk import (  # noqa: E402
    _close_trade,
    _goal_for_walk,
    summarize_equity,
)
from agent.schema import Goal, PaperTrade  # noqa: E402
from agent.waiting_deal_walk import _proposal_from_out  # noqa: E402
from agent.walk_exec import (  # noqa: E402
    RestPending,
    check_exit_rest,
    fill_mode_of,
    may_check_exit,
    realize_rest_trade,
    window_end_price,
)
from agent import policy  # noqa: E402
from app import oanda_client, regime as regime_mod, regime_walk  # noqa: E402
from app.session_clock import waiting_deal_day_outcomes  # noqa: E402

FROM_T = "2015-01-01T00:00:00Z"
TO_T = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
D_FROM = "2014-01-01T00:00:00Z"
# GBP_SHF in the request is GBP_CHF.
PAIRS = ("GBP_USD", "EUR_GBP", "GBP_CHF", "GBP_JPY")
LOOKBACK = 250
LTF_TAIL = 288  # ~3 FX days of M15; enough for one London date
START_EQUITY = 10_000.0
RISK_FRAC = 0.02
VPPU = 1.0
CLOCK = "frankfurt"
HUNT_PIPS = deal_mod.HUNT_PIPS

SUMMARY_PATH = OUT / "comparison.json"


def slug(instrument: str) -> str:
    return instrument.lower()


def pair_paths(instrument: str) -> dict[str, Path]:
    s = slug(instrument)
    return {
        "d": OUT / f"{s}_d.json",
        "m15": OUT / f"{s}_m15.json",
        "trades": OUT / f"{s}_trades.json",
        "campaign": OUT / f"{s}_campaign.json",
    }


def _compact_d(bar: dict) -> dict:
    return {
        "time": bar["time"],
        "open": bar["open"],
        "high": bar["high"],
        "low": bar["low"],
        "close": bar["close"],
        "complete": True,
    }


def _compact_ba(bar: dict) -> dict | None:
    bid = bar.get("bid")
    ask = bar.get("ask")
    if not isinstance(bid, dict) or not isinstance(ask, dict):
        return None
    return {
        "time": bar["time"],
        "open": bar["open"],
        "high": bar["high"],
        "low": bar["low"],
        "close": bar["close"],
        "complete": True,
        "bid": {"o": bid["o"], "h": bid["h"], "l": bid["l"], "c": bid["c"]},
        "ask": {"o": ask["o"], "h": ask["h"], "l": ask["l"], "c": ask["c"]},
    }


async def load_daily(instrument: str, cache: Path) -> list[dict]:
    if cache.exists():
        bars = json.loads(cache.read_text())
        print(f"CACHE {cache.name} n={len(bars)} last={bars[-1]['time']}", flush=True)
        return bars
    print(f"FETCH {instrument} D {D_FROM} .. {TO_T}", flush=True)
    payload = await oanda_client.get_candles(
        instrument,
        granularity="D",
        count=None,
        price="M",
        from_time=D_FROM,
        to_time=TO_T,
    )
    bars = [
        _compact_d(b)
        for b in oanda_client.candles_to_bars(payload, prefer="mid")
        if b.get("complete", True)
    ]
    cache.write_text(json.dumps(bars))
    print(f"WROTE {cache.name} n={len(bars)}", flush=True)
    return bars


async def load_m15_ba(instrument: str, cache: Path) -> list[dict]:
    if cache.exists():
        bars = json.loads(cache.read_text())
        print(f"CACHE {cache.name} n={len(bars)} last={bars[-1]['time']}", flush=True)
        return bars
    print(f"FETCH {instrument} M15 MBA {FROM_T} .. {TO_T}", flush=True)
    payload = await oanda_client.get_candles(
        instrument,
        granularity="M15",
        count=None,
        price="MBA",
        from_time=FROM_T,
        to_time=TO_T,
    )
    bars = []
    for b in oanda_client.bars_with_ba(payload):
        if not b.get("complete", True):
            continue
        compact = _compact_ba(b)
        if compact is not None:
            bars.append(compact)
    cache.write_text(json.dumps(bars))
    print(f"WROTE {cache.name} n={len(bars)}", flush=True)
    return bars


def year_of(ts: str) -> str:
    return (ts or "")[:4]


def build_htf_cache(instrument: str, htf_bars: list[dict]) -> dict[str, dict]:
    cache: dict[str, dict] = {}
    series = regime_walk.drop_incomplete(htf_bars)
    n = len(series)
    print(f"HTF classify {instrument} n={n} lookback={LOOKBACK}", flush=True)
    for i in range(LOOKBACK - 1, n):
        window = series[: i + 1][-LOOKBACK:]
        t = str(series[i].get("time") or "")
        analysis = dict(regime_mod.analyze_bars(window))
        analysis.setdefault("instrument", instrument)
        analysis.setdefault("granularity", "D")
        cache[t] = analysis
        if (i - LOOKBACK + 1) % 500 == 0:
            print(f"  htf {instrument} {i + 1}/{n} {t}", flush=True)
    print(f"HTF cache {instrument} {len(cache)}", flush=True)
    return cache


def htf_as_of(cache: dict[str, dict], times: list[str], t: str) -> dict | None:
    import bisect

    i = bisect.bisect_right(times, t) - 1
    if i < 0:
        return None
    return cache.get(times[i])


def walk_campaign(
    instrument: str,
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
            instrument=instrument,
            granularity="D",
            ltf_granularity="M15",
            mode="paper",
            from_time=FROM_T,
            to_time=TO_T,
            risk_fraction=RISK_FRAC,
            balance=START_EQUITY,
            value_per_price_unit=VPPU,
            fill_mode="rest",
            no_rag=True,
            no_llm=True,
        )
    )
    walk_id = uuid.uuid4().hex
    equity = START_EQUITY
    trades: list[PaperTrade] = []
    open_trade: PaperTrade | None = None
    pending: RestPending | None = None
    prev_fired = False
    last_i = len(ltf_series) - 1
    fill_mode = fill_mode_of(goal)
    n_eval = 0
    n_gate_ok = 0
    n_fire = 0
    n_policy_block = 0
    n_skip_fill = 0
    play_counts: Counter[str] = Counter()

    print(
        f"WALK {instrument} M15 rest start_index={start_index}/{len(ltf_series)} "
        f"from={ltf_series[start_index].get('time')}",
        flush=True,
    )
    for i in range(start_index, len(ltf_series)):
        bar = ltf_series[i]
        bar_time = str(bar.get("time") or "")
        if i % 20000 == 0:
            print(
                f"  ltf {instrument} {i}/{last_i} {bar_time} trades={len(trades)}",
                flush=True,
            )

        if pending is not None and i == pending.fill_index:
            realized = realize_rest_trade(
                pending, bar, i, bar_time, equity, goal, walk_id
            )
            if realized is not None:
                open_trade, _fill = realized
            else:
                n_skip_fill += 1
            pending = None

        exited_here = False
        if open_trade is not None and may_check_exit(
            open_trade.entry_index, i, fill_mode
        ):
            hit = check_exit_rest(
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

        if open_trade is None and pending is None and not exited_here:
            htf_analysis = htf_as_of(htf_cache, htf_times, bar_time)
            fired = False
            out = None
            ticket = None
            if htf_analysis is not None:
                n_eval += 1
                allowed = set(htf_analysis.get("allowed_play_classes") or [])
                gate_ok = (
                    not htf_analysis.get("trend_waning")
                    and bool(allowed & {"fade_range", "breakout_watch"})
                )
                if gate_ok:
                    n_gate_ok += 1
                    ltf_window = ltf_series[max(0, i + 1 - LTF_TAIL) : i + 1]
                    out = deal_mod.waiting_deal_signal(
                        htf_analysis,
                        ltf_window,
                        instrument,
                        hunt_pips=HUNT_PIPS,
                        clock=CLOCK,  # type: ignore[arg-type]
                        htf_granularity="D",
                        ltf_granularity="M15",
                    )
                    ticket = out.get("ticket")
                    fired = bool(
                        out
                        and out["signal"] in ("long", "short")
                        and ticket
                    )
            if fired and not prev_fired:
                assert out is not None and ticket is not None and htf_analysis is not None
                n_fire += 1
                proposal = _proposal_from_out(out, ticket, bar_time)
                verdict = policy.evaluate(htf_analysis, proposal, goal)
                if verdict.ok:
                    play_counts[str(proposal.play_class)] += 1
                    if i + 1 < len(ltf_series):
                        pending = RestPending(
                            fill_index=i + 1,
                            side=proposal.side,
                            play_class=proposal.play_class,
                            stop=float(ticket["stop"]),
                            target=float(ticket["target"]),
                            reasons=list(verdict.reasons),
                            proposal=proposal,
                            verdict=verdict,
                            analysis=htf_analysis,
                        )
                    else:
                        n_skip_fill += 1
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
        "n_gate_ok": n_gate_ok,
        "n_fire": n_fire,
        "n_policy_block": n_policy_block,
        "n_skip_fill": n_skip_fill,
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
        plays = Counter(t.play_class for t in chunk)
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
                "play_class": dict(plays),
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


def play_block(trades: list[PaperTrade], play: str) -> dict:
    chunk = [t for t in trades if t.play_class == play]
    rs = [float(t.r_realized) for t in chunk if t.r_realized is not None]
    n = len(rs)
    return {
        "n": len(chunk),
        "mean_r": round(sum(rs) / n, 4) if n else None,
        "win_rate": round(sum(1 for r in rs if r > 0) / n, 4) if n else None,
        "sum_r": round(sum(rs), 4) if n else None,
    }


def hold_hours(trades: list[PaperTrade]) -> tuple[float | None, float | None]:
    holds = []
    for t in trades:
        if t.entry_time and t.exit_time:
            a = regime_walk._parse_rfc3339_utc(t.entry_time)
            b = regime_walk._parse_rfc3339_utc(t.exit_time)
            if a is not None and b is not None:
                holds.append((b - a) / 3600.0)
    if not holds:
        return None, None
    return (
        round(sum(holds) / len(holds), 2),
        round(sorted(holds)[len(holds) // 2], 2),
    )


async def run_pair(instrument: str) -> dict:
    paths = pair_paths(instrument)
    if paths["campaign"].exists():
        summary = json.loads(paths["campaign"].read_text())
        print(
            f"SKIP {instrument} existing campaign n={summary.get('trade_count')}",
            flush=True,
        )
        return summary

    d_bars = await load_daily(instrument, paths["d"])
    m15_bars = await load_m15_ba(instrument, paths["m15"])
    htf_cache = build_htf_cache(instrument, d_bars)
    trades, meta = walk_campaign(instrument, d_bars, m15_bars, htf_cache)
    equity = summarize_equity(meta["walk_id"], trades, START_EQUITY, RISK_FRAC)
    pip = pip_size(instrument)
    print(f"microstructure {instrument}…", flush=True)
    clock_stats = waiting_deal_day_outcomes(m15_bars, pip, clock=CLOCK)
    dump = [t.model_dump(mode="json") for t in trades]
    paths["trades"].write_text(json.dumps(dump, indent=2))
    mean_h, med_h = hold_hours(trades)
    summary = {
        "instrument": instrument,
        "engine": "waiting_deal",
        "chapter": 11,
        "clock": CLOCK,
        "fill_mode": "rest",
        "hunt_pips": HUNT_PIPS,
        "pip_size": pip,
        "note": (
            "Causal Ch.11 paper walk, rest fill. Daily Ch.7 gate "
            "(fade_range or breakout_watch); M15 Frankfurt–London power hour, "
            "≥25-pip hunt, reverse through opposite rail. Next-bar long ask / "
            "short bid open; making-side exit. 2R tickets, 2% equity. "
            "25-pip hunt is the book GBPUSD number on every pair. "
            "News/FOMC not filtered. Not orders."
        ),
        "citations": [
            {"source": "lien-fx", "chunk_index": 80},
            {"source": "lien-fx", "chunk_index": 81},
            {"source": "lien-fx", "chunk_index": 82},
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
        "by_play_class": {
            "fade_range": play_block(trades, "fade_range"),
            "breakout_watch": play_block(trades, "breakout_watch"),
        },
        "mean_hold_hours": mean_h,
        "median_hold_hours": med_h,
        "years": year_rows(trades, START_EQUITY),
        "clock_stats": clock_stats,
        **meta,
    }
    paths["campaign"].write_text(json.dumps(summary, indent=2, default=str))
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
                    "n_skip_fill",
                    "clock_stats",
                )
            },
            indent=2,
            default=str,
        ),
        flush=True,
    )
    print(f"WROTE {paths['campaign']}", flush=True)
    return summary


def comparison_row(summary: dict) -> dict:
    eq = summary.get("equity") or {}
    clock = summary.get("clock_stats") or {}
    return {
        "instrument": summary["instrument"],
        "n": summary.get("trade_count"),
        "n_fire": summary.get("n_fire"),
        "n_skip_fill": summary.get("n_skip_fill"),
        "ending_equity": eq.get("ending_equity"),
        "mean_r": eq.get("mean_r"),
        "sum_r": eq.get("sum_r"),
        "win_rate": eq.get("win_rate"),
        "max_drawdown_frac": eq.get("max_drawdown_frac"),
        "wins": eq.get("wins"),
        "losses": eq.get("losses"),
        "exits": summary.get("exits"),
        "by_side": summary.get("by_side"),
        "by_play_class": summary.get("by_play_class"),
        "clock_days": clock.get("days_with_power_hour"),
        "hunt_rate": clock.get("hunt_rate"),
        "reverse_rate": clock.get("reverse_rate"),
        "reverse_given_hunt": clock.get("reverse_rate_given_hunt"),
        "mean_hold_hours": summary.get("mean_hold_hours"),
        "median_hold_hours": summary.get("median_hold_hours"),
        "pip_size": summary.get("pip_size"),
        "last_m15": summary.get("last_m15"),
    }


async def main() -> int:
    summaries: list[dict] = []
    for instrument in PAIRS:
        summaries.append(await run_pair(instrument))
    comparison = {
        "engine": "waiting_deal",
        "chapter": 11,
        "fill_mode": "rest",
        "window": f"{FROM_T} to {TO_T}",
        "pairs": list(PAIRS),
        "hunt_pips": HUNT_PIPS,
        "value_per_price_unit": VPPU,
        "starting_equity": START_EQUITY,
        "risk_fraction": RISK_FRAC,
        "clock": CLOCK,
        "note": (
            "Same Ch.11 geometry and 25-pip hunt on every pair. Rest fill. "
            "vppu=1.0 so dollar equity is 2%×R compounding, not a USD-account "
            "JPY conversion. GBP_SHF in the request is GBP_CHF. Research only."
        ),
        "rows": [comparison_row(s) for s in summaries],
    }
    SUMMARY_PATH.write_text(json.dumps(comparison, indent=2, default=str))
    print(json.dumps(comparison, indent=2, default=str), flush=True)
    print(f"WROTE {SUMMARY_PATH}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
