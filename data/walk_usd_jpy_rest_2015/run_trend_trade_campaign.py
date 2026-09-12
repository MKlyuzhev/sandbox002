"""Causal trend_trade campaign: USD_JPY 2015-now, Lien-clock dual D+H1.

Research only; no orders. Kalman Q is the frozen Lien prior (not PnL-searched).
Ch.7 ``join_trend`` (not waning) gates entries. ATR 14×3 sizes 2% equity.
Half-year slices match the rest-fill MTF campaign windowing.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parent
_S002 = OUT.parents[1]
if str(OUT) not in sys.path:
    sys.path.insert(0, str(OUT))
if str(_S002) not in sys.path:
    sys.path.insert(0, str(_S002))

import run_kalman_lien_clock as kalman  # noqa: E402
from app import oanda_client  # noqa: E402
from trend_strategy_defaults import (  # noqa: E402
    LienH1DipConfig,
    SSM_KALMAN_PARAMS_LIEN_D1,
    SSM_KALMAN_PARAMS_LIEN_H1_TIMING,
    h1_dip_entry_masks,
)

FROM_T = "2015-01-01T00:00:00Z"
TO_T = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
INSTRUMENT = "USD_JPY"
PIP = 0.01
VPPU = 1.0 / 150.0
START_EQUITY = 10_000.0
RISK_FRAC = 0.02
ATR_PERIOD = 14
ATR_K = 3.0
Z_EXIT = 0.25

TRADES_PATH = OUT / "trend_trade_trades.json"
SLICES_PATH = OUT / "trend_trade_slices.json"
SUMMARY_PATH = OUT / "trend_trade_campaign.json"


def slices() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    end_year = datetime.now(timezone.utc).year
    for y in range(2015, end_year + 1):
        a = f"{y}-01-01T00:00:00Z"
        b = f"{y}-07-01T00:00:00Z"
        out.append((a, b))
        a2 = b
        b2 = TO_T if y == end_year else f"{y + 1}-01-01T00:00:00Z"
        out.append((a2, b2))
    return out


def wilder_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    prev = np.empty_like(close)
    prev[0] = close[0]
    prev[1:] = close[:-1]
    tr = np.maximum.reduce(
        [high - low, np.abs(high - prev), np.abs(low - prev)]
    )
    atr = np.full(close.shape, np.nan, dtype=float)
    if close.size < period:
        return atr
    atr[period - 1] = float(np.mean(tr[:period]))
    alpha = 1.0 / float(period)
    for i in range(period, close.size):
        atr[i] = (1.0 - alpha) * atr[i - 1] + alpha * tr[i]
    return atr


def arrays_ohlc(bars: list[dict]) -> dict:
    return {
        "t": np.array([kalman._unix(b["time"]) for b in bars], dtype=float),
        "open": np.array([b["open"] for b in bars], dtype=float),
        "high": np.array([b["high"] for b in bars], dtype=float),
        "low": np.array([b["low"] for b in bars], dtype=float),
        "close": np.array([b["close"] for b in bars], dtype=float),
        "time": [b["time"] for b in bars],
    }


async def load_ohlc_to_now(granularity: str, cache: Path) -> list[dict]:
    if cache.exists():
        bars = json.loads(cache.read_text())
        last = bars[-1]["time"] if bars else None
        print(f"SKIP fetch {granularity} last={last} n={len(bars)}", flush=True)
        return bars
    print(f"FETCH {INSTRUMENT} {granularity} {FROM_T} .. {TO_T}", flush=True)
    payload = await oanda_client.get_candles(
        INSTRUMENT,
        granularity=granularity,
        count=None,
        price="M",
        from_time=FROM_T,
        to_time=TO_T,
    )
    bars = [
        {
            "time": b["time"],
            "open": b["open"],
            "high": b["high"],
            "low": b["low"],
            "close": b["close"],
        }
        for b in oanda_client.candles_to_bars(payload, prefer="mid")
        if b.get("complete", True)
    ]
    cache.write_text(json.dumps(bars))
    print(f"WROTE {cache.name} n={len(bars)}", flush=True)
    return bars


def run_dual_campaign(
    h: dict,
    daily_z_al: np.ndarray,
    h1_z: np.ndarray,
    long_pulse: np.ndarray,
    short_pulse: np.ndarray,
    long_gate: np.ndarray,
    short_gate: np.ndarray,
    atr_h1: np.ndarray,
    regime_h: np.ndarray,
    dir_h: np.ndarray,
    wan_h: np.ndarray,
) -> list[dict]:
    n = h["open"].size
    trades: list[dict] = []
    equity = START_EQUITY
    peak = START_EQUITY
    max_dd = 0.0
    pos = 0
    entry_i = -1
    entry_px = 0.0
    stop_px = 0.0
    risk_cash = 0.0
    units = 0.0
    entry_h1_z = 0.0
    entry_daily_z = 0.0
    entry_atr = 0.0
    for i in range(n - 1):
        fill = i + 1
        dz = float(daily_z_al[i])
        if pos == 0:
            atr = float(atr_h1[i])
            if not np.isfinite(atr) or atr <= 0:
                continue
            dist = ATR_K * atr
            if bool(long_pulse[i]) and bool(long_gate[i]) and np.isfinite(dz):
                pos = 1
            elif bool(short_pulse[i]) and bool(short_gate[i]) and np.isfinite(dz):
                pos = -1
            else:
                continue
            entry_i = fill
            entry_px = float(h["open"][fill])
            stop_px = entry_px - pos * dist
            risk_cash = RISK_FRAC * equity
            units = risk_cash / max(dist * VPPU, 1e-12)
            entry_h1_z = float(h1_z[i])
            entry_daily_z = dz
            entry_atr = atr
            continue

        hi = float(h["high"][fill])
        lo = float(h["low"][fill])
        stopped = (lo <= stop_px) if pos == 1 else (hi >= stop_px)
        weak = dz < Z_EXIT if pos == 1 else dz > -Z_EXIT
        gate_off = (not bool(long_gate[i])) if pos == 1 else (not bool(short_gate[i]))
        if not (stopped or weak or gate_off):
            continue
        if stopped:
            exit_px = stop_px
            reason = "STOP_atr"
        else:
            exit_px = float(h["open"][fill])
            reason = "gate" if gate_off and not weak else "EXIT_daily_slope_weak"
        pnl_price = (exit_px - entry_px) * pos
        pnl_cash = units * pnl_price * VPPU
        r = pnl_cash / risk_cash if risk_cash else 0.0
        equity += pnl_cash
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)
        trades.append(
            {
                "side": "long" if pos == 1 else "short",
                "entry_time": h["time"][entry_i],
                "exit_time": h["time"][fill],
                "entry": round(entry_px, 5),
                "exit": round(exit_px, 5),
                "stop": round(stop_px, 5),
                "duration_h1": fill - entry_i,
                "duration_days": round((fill - entry_i) / 24.0, 3),
                "pnl_pips": round(pnl_price / PIP, 2),
                "pnl": round(pnl_cash, 4),
                "r": round(r, 4),
                "equity": round(equity, 4),
                "exit_status": reason,
                "entry_h1_z": round(entry_h1_z, 4),
                "entry_daily_z": round(entry_daily_z, 4),
                "atr": round(entry_atr, 5),
                "regime": str(regime_h[entry_i]),
                "direction": str(dir_h[entry_i]),
                "trend_waning": bool(wan_h[entry_i]),
                "year": str(h["time"][entry_i])[:4],
            }
        )
        pos = 0
    return trades, max_dd, equity


def bucket(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    rs = [float(t["r"]) for t in rows]
    wins = sum(1 for r in rs if r > 0)
    pips = [float(t["pnl_pips"]) for t in rows]
    durs = [float(t["duration_days"]) for t in rows]
    return {
        "n": n,
        "mean_r": round(sum(rs) / n, 4),
        "sum_r": round(sum(rs), 4),
        "win_rate": round(wins / n, 4),
        "wins": wins,
        "losses": n - wins,
        "mean_pnl_pips": round(sum(pips) / n, 2),
        "sum_pnl_pips": round(sum(pips), 2),
        "mean_duration_days": round(sum(durs) / n, 3),
        "median_duration_days": round(float(np.median(durs)), 3),
    }


def slice_index(entry_time: str, windows: list[tuple[str, str]]) -> int:
    t = kalman._unix(entry_time)
    for i, (a, b) in enumerate(windows):
        if kalman._unix(a) <= t < kalman._unix(b):
            return i
    return len(windows) - 1


async def main() -> None:
    steps = json.loads(kalman.STEPS_PATH.read_text())
    daily_bars = await load_ohlc_to_now("D", kalman.DAILY_CACHE)
    h1_bars = await load_ohlc_to_now("H1", kalman.H1_CACHE)
    d = arrays_ohlc(daily_bars)
    h = arrays_ohlc(h1_bars)
    print(f"bars daily={d['close'].size} h1={h['close'].size}", flush=True)

    regime_d, dir_d, wan_d = kalman.align_ch7(d["t"], steps)
    long_g_d, short_g_d = kalman.join_trend_masks(regime_d, dir_d, wan_d)

    print("FIT daily LLT + H1 timing (causal filt)", flush=True)
    z_llt = kalman.fit_slope_z(d["close"], SSM_KALMAN_PARAMS_LIEN_D1)
    z_h1 = kalman.fit_slope_z(h["close"], SSM_KALMAN_PARAMS_LIEN_H1_TIMING)
    daily_z_h1 = kalman.align_asof_float(d["t"], z_llt, h["t"])
    long_g_h = kalman.align_asof_float(d["t"], long_g_d.astype(float), h["t"]) > 0.5
    short_g_h = kalman.align_asof_float(d["t"], short_g_d.astype(float), h["t"]) > 0.5
    regime_h = kalman.align_asof_obj(d["t"], regime_d, h["t"])
    dir_h = kalman.align_asof_obj(d["t"], dir_d, h["t"])
    wan_h = kalman.align_asof_float(d["t"], wan_d.astype(float), h["t"]) > 0.5
    atr_d = wilder_atr(d["high"], d["low"], d["close"], ATR_PERIOD)
    atr_h1 = kalman.align_asof_float(d["t"], atr_d, h["t"])

    dip_cfg = LienH1DipConfig(z_entry_daily=1.0, z_dip=0.25, warmup_bars=50)
    long_p, short_p = h1_dip_entry_masks(daily_z_h1, z_h1, dip_cfg)

    trades, max_dd, end_eq = run_dual_campaign(
        h,
        daily_z_h1,
        z_h1,
        long_p,
        short_p,
        long_g_h,
        short_g_h,
        atr_h1,
        regime_h,
        dir_h,
        wan_h,
    )

    windows = slices()
    for t in trades:
        t["slice_i"] = slice_index(t["entry_time"], windows)

    slice_rows = []
    eq0 = START_EQUITY
    by_slice: dict[int, list] = defaultdict(list)
    for t in trades:
        by_slice[int(t["slice_i"])].append(t)
    for i, (a, b) in enumerate(windows):
        rows = by_slice.get(i, [])
        end = float(rows[-1]["equity"]) if rows else eq0
        rec = {
            "i": i,
            "from_time": a,
            "to_time": b,
            "starting_equity": round(eq0, 4),
            "ending_equity": round(end, 4),
            **{k: v for k, v in bucket(rows).items()},
        }
        slice_rows.append(rec)
        eq0 = end

    by_year: dict[str, list] = defaultdict(list)
    by_reg: dict[str, list] = defaultdict(list)
    by_reason: dict[str, int] = defaultdict(int)
    for t in trades:
        by_year[t["year"]].append(t)
        by_reg[t["regime"]].append(t)
        by_reason[t["exit_status"]] += 1

    last_d = d["time"][-1]
    last_h = h["time"][-1]
    end_year = int(str(last_h)[:4])
    yearly = [
        {"year": str(y), **bucket(by_year.get(str(y), []))}
        for y in range(2015, end_year + 1)
    ]
    same_sign = sum(
        1
        for t in trades
        if np.sign(t["entry_h1_z"]) == np.sign(t["entry_daily_z"])
    )
    smaller = sum(
        1
        for t in trades
        if abs(t["entry_h1_z"]) < abs(t["entry_daily_z"])
    )
    n = len(trades)
    summary = {
        "instrument": INSTRUMENT,
        "engine": "trend_trade_lien_clock_dual",
        "note": (
            "Causal filt Kalman. Daily LLT owns direction (z_entry=1.0); "
            "H1 dip is timing only; Q from Lien D1 Set B /24. Not a Lien chapter."
        ),
        "window": f"{FROM_T} to {last_h}",
        "last_daily_bar": last_d,
        "fill_mode": "next_open",
        "ch7": "join_trend not waning (entry gate + flatten)",
        "value_per_price_unit": VPPU,
        "starting_equity": START_EQUITY,
        "risk_fraction": RISK_FRAC,
        "atr": {"period": ATR_PERIOD, "k": ATR_K},
        "ssm": {
            "daily": {k: SSM_KALMAN_PARAMS_LIEN_D1[k] for k in SSM_KALMAN_PARAMS_LIEN_D1},
            "h1_q_slope": SSM_KALMAN_PARAMS_LIEN_H1_TIMING["q_slope"],
        },
        "n_daily": int(d["close"].size),
        "n_h1": int(h["close"].size),
        "trade_count": n,
        "ending_equity": round(end_eq, 4),
        "max_drawdown_frac": round(max_dd, 4),
        **bucket(trades),
        "exits": dict(by_reason),
        "by_regime": {rg: bucket(rows) for rg, rows in sorted(by_reg.items())},
        "yearly": yearly,
        "pullback": {
            "frac_same_sign_as_daily": round(same_sign / n, 4) if n else None,
            "frac_abs_h1_z_lt_abs_daily_z": round(smaller / n, 4) if n else None,
        },
        "sliced": True,
    }
    TRADES_PATH.write_text(json.dumps(trades, indent=2) + "\n")
    SLICES_PATH.write_text(json.dumps(slice_rows, indent=2) + "\n")
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in (
        "instrument", "window", "trade_count", "ending_equity",
        "mean_r", "win_rate", "max_drawdown_frac", "mean_duration_days",
        "exits", "by_regime", "yearly", "pullback",
    ) if k in summary}, indent=2), flush=True)
    print(f"WROTE {SUMMARY_PATH}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
