"""Causal cycle_trade campaign: USD_JPY 2015-now, daily Murphy 10/20/40.

Research only; no orders. Kalman Q is the frozen Lien-clock cycle prior
(not PnL-searched). Ch.7 ``fade_range`` (regime range, not waning) gates
entries and flattens on leave-range. ATR 14×3 sizes 2% equity.
Half-year slices match the rest-fill / trend_trade campaign windowing.
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
_S001 = Path("/home/maxim/Projects/sandbox001")
if str(OUT) not in sys.path:
    sys.path.insert(0, str(OUT))
if str(_S002) not in sys.path:
    sys.path.insert(0, str(_S002))
_cyc = _S001 / "SB_QuantAna" / "cycle_trade"
if str(_cyc) not in sys.path:
    sys.path.insert(0, str(_cyc))

import run_kalman_lien_clock as kalman  # noqa: E402
import ssm_lib  # noqa: E402
from cycle_strategy_defaults import (  # noqa: E402
    SSM_KALMAN_PARAMS_LIEN_D1_CYCLE,
    kalman_kwargs_from_ssm_params,
)

FROM_T = kalman.FROM_T
TO_T = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
INSTRUMENT = "USD_JPY"
PIP = 0.01
VPPU = 1.0 / 150.0
START_EQUITY = 10_000.0
RISK_FRAC = 0.02
ATR_PERIOD = 14
ATR_K = 3.0
T_MAX = 30
WARMUP = 80
AMP_MIN_FRAC = 0.5
AMP_ROLLING = 20
ALIGNMENT_MIN = 2
FAST_PHASE_LONG = (-np.pi, -1.0)
FAST_PHASE_SHORT = (1.0, np.pi)

TRADES_PATH = OUT / "cycle_trade_trades.json"
SLICES_PATH = OUT / "cycle_trade_slices.json"
SUMMARY_PATH = OUT / "cycle_trade_campaign.json"


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
    tr = np.maximum.reduce([high - low, np.abs(high - prev), np.abs(low - prev)])
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


def fade_range_mask(regime: np.ndarray, waning: np.ndarray) -> np.ndarray:
    return (regime == "range") & (~waning)


def rolling_median(x: np.ndarray, window: int) -> np.ndarray:
    n = x.size
    out = np.full(n, np.nan)
    minp = max(10, window // 4)
    for i in range(minp - 1, n):
        out[i] = float(np.median(x[max(0, i - window + 1) : i + 1]))
    return out


def cycle_signals(y: np.ndarray, res: dict) -> dict[str, np.ndarray]:
    """Numpy port of trade_cycle.compute_cycle_signals (filt, alignment on, no pandas)."""
    comps = res["components_filt"]
    c0 = np.asarray(comps["cycle_0_c"], dtype=float)
    s0 = np.asarray(comps["cycle_0_s"], dtype=float)
    c1 = np.asarray(comps["cycle_1_c"], dtype=float)
    s1 = np.asarray(comps["cycle_1_s"], dtype=float)
    c2 = np.asarray(comps["cycle_2_c"], dtype=float)
    s2 = np.asarray(comps["cycle_2_s"], dtype=float)
    t = y.size
    phase0 = np.arctan2(c0, s0)
    amp0 = np.sqrt(c0 * c0 + s0 * s0)
    amp1 = np.sqrt(c1 * c1 + s1 * s1)
    amp2 = np.sqrt(c2 * c2 + s2 * s2)
    cycle_composite = c0 + c1 + c2
    cycle_mom = np.full(t, np.nan)
    cycle_mom[1:] = cycle_composite[1:] - cycle_composite[:-1]
    mom_prev = np.full(t, np.nan)
    mom_prev[1:] = cycle_mom[:-1]
    long_turn = (mom_prev < 0) & (cycle_mom >= 0)
    short_turn = (mom_prev > 0) & (cycle_mom <= 0)
    amp_composite = np.sqrt(amp0**2 + amp1**2 + amp2**2)
    amp_med = rolling_median(amp_composite, AMP_ROLLING)
    amp_norm = amp_composite / np.where(amp_med > 0, amp_med, np.nan)
    amp_gate = amp_norm > AMP_MIN_FRAC
    c0_mom = np.full(t, np.nan)
    c1_mom = np.full(t, np.nan)
    c2_mom = np.full(t, np.nan)
    c0_mom[1:] = c0[1:] - c0[:-1]
    c1_mom[1:] = c1[1:] - c1[:-1]
    c2_mom[1:] = c2[1:] - c2[:-1]
    long_agree = (c0_mom >= 0).astype(int) + (c1_mom >= 0).astype(int) + (c2_mom >= 0).astype(int)
    short_agree = (c0_mom <= 0).astype(int) + (c1_mom <= 0).astype(int) + (c2_mom <= 0).astype(int)
    alignment_long = long_agree >= ALIGNMENT_MIN
    alignment_short = short_agree >= ALIGNMENT_MIN
    phase_long = (phase0 >= FAST_PHASE_LONG[0]) & (phase0 <= FAST_PHASE_LONG[1])
    phase_short = (phase0 >= FAST_PHASE_SHORT[0]) & (phase0 <= FAST_PHASE_SHORT[1])
    entry_long = long_turn & amp_gate & alignment_long & phase_long
    entry_short = short_turn & amp_gate & alignment_short & phase_short
    if WARMUP > 0:
        w_bars = min(WARMUP, t)
        entry_long[:w_bars] = False
        entry_short[:w_bars] = False
    exit_long_tp = (mom_prev > 0) & (cycle_mom <= 0)
    exit_short_tp = (mom_prev < 0) & (cycle_mom >= 0)
    slope = np.asarray(comps["slope"], dtype=float)
    var = np.asarray(res["P_filt"][:, 1, 1], dtype=float)
    slope_z = slope / np.sqrt(np.maximum(var, 1e-18))
    return {
        "entry_long": entry_long.astype(bool),
        "entry_short": entry_short.astype(bool),
        "exit_long_tp": exit_long_tp.astype(bool),
        "exit_short_tp": exit_short_tp.astype(bool),
        "amp_gate": amp_gate.astype(bool),
        "alignment_long": alignment_long.astype(bool),
        "alignment_short": alignment_short.astype(bool),
        "slope_z": slope_z,
        "agree_long": long_agree,
        "agree_short": short_agree,
    }


def run_daily_campaign(
    d: dict,
    sig: dict[str, np.ndarray],
    gate: np.ndarray,
    atr: np.ndarray,
    regime: np.ndarray,
    direction: np.ndarray,
    waning: np.ndarray,
) -> tuple[list[dict], float, float]:
    n = d["open"].size
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
    entry_atr = 0.0
    entry_z = 0.0
    entry_agree = 0
    entry_regime = ""
    entry_dir = ""
    entry_wan = False
    for i in range(n - 1):
        fill = i + 1
        if pos == 0:
            atr_i = float(atr[i])
            if not np.isfinite(atr_i) or atr_i <= 0:
                continue
            if not bool(gate[i]):
                continue
            if bool(sig["entry_long"][i]):
                pos = 1
                entry_agree = int(sig["agree_long"][i])
            elif bool(sig["entry_short"][i]):
                pos = -1
                entry_agree = int(sig["agree_short"][i])
            else:
                continue
            dist = ATR_K * atr_i
            entry_i = fill
            entry_px = float(d["open"][fill])
            stop_px = entry_px - pos * dist
            risk_cash = RISK_FRAC * equity
            units = risk_cash / max(dist * VPPU, 1e-12)
            entry_atr = atr_i
            entry_z = float(sig["slope_z"][i])
            entry_regime = str(regime[i])
            entry_dir = str(direction[i])
            entry_wan = bool(waning[i])
            continue

        hi = float(d["high"][fill])
        lo = float(d["low"][fill])
        stopped = (lo <= stop_px) if pos == 1 else (hi >= stop_px)
        flip = bool(sig["entry_short"][i]) if pos == 1 else bool(sig["entry_long"][i])
        tp = bool(sig["exit_long_tp"][i]) if pos == 1 else bool(sig["exit_short_tp"][i])
        timed = (fill - entry_i) >= T_MAX
        gate_off = not bool(gate[i])
        if not (stopped or flip or tp or timed or gate_off):
            continue
        if stopped:
            exit_px = stop_px
            reason = "STOP_atr"
        elif flip:
            exit_px = float(d["open"][fill])
            reason = "FLIP_to_short" if pos == 1 else "FLIP_to_long"
        elif tp:
            exit_px = float(d["open"][fill])
            reason = "TP_half_cycle"
        elif timed:
            exit_px = float(d["open"][fill])
            reason = "TIME_stop"
        else:
            exit_px = float(d["open"][fill])
            reason = "gate"
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
                "entry_time": d["time"][entry_i],
                "exit_time": d["time"][fill],
                "entry": round(entry_px, 5),
                "exit": round(exit_px, 5),
                "stop": round(stop_px, 5),
                "duration_days": fill - entry_i,
                "pnl_pips": round(pnl_price / PIP, 2),
                "pnl": round(pnl_cash, 4),
                "r": round(r, 4),
                "equity": round(equity, 4),
                "exit_status": reason,
                "entry_daily_z": round(entry_z, 4),
                "entry_agree": entry_agree,
                "atr": round(entry_atr, 5),
                "regime": entry_regime,
                "fill_regime": str(regime[entry_i]),
                "direction": entry_dir,
                "trend_waning": entry_wan,
                "year": str(d["time"][entry_i])[:4],
            }
        )
        closed_side = pos
        pos = 0
        if reason.startswith("FLIP") and bool(gate[i]):
            atr_i = float(atr[i])
            if np.isfinite(atr_i) and atr_i > 0:
                pos = -1 if closed_side == 1 else 1
                dist = ATR_K * atr_i
                entry_i = fill
                entry_px = float(d["open"][fill])
                stop_px = entry_px - pos * dist
                risk_cash = RISK_FRAC * equity
                units = risk_cash / max(dist * VPPU, 1e-12)
                entry_atr = atr_i
                entry_z = float(sig["slope_z"][i])
                entry_agree = int(sig["agree_short"][i] if pos == -1 else sig["agree_long"][i])
                entry_regime = str(regime[i])
                entry_dir = str(direction[i])
                entry_wan = bool(waning[i])
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
    daily_bars = await kalman.load_ohlc("D", kalman.DAILY_CACHE)
    d = arrays_ohlc(daily_bars)
    print(f"bars daily={d['close'].size}", flush=True)

    regime_d, dir_d, wan_d = kalman.align_ch7(d["t"], steps)
    gate = fade_range_mask(regime_d, wan_d)
    atr = wilder_atr(d["high"], d["low"], d["close"], ATR_PERIOD)

    print("FIT daily cycle SSM [10,20,40] (causal filt)", flush=True)
    y = np.log(np.asarray(d["close"], dtype=float))
    kwargs = kalman_kwargs_from_ssm_params(SSM_KALMAN_PARAMS_LIEN_D1_CYCLE)
    kwargs["do_smooth"] = False
    res = ssm_lib.fit_price_components_kalman(y=y, dt=1.0, **kwargs)
    sig = cycle_signals(y, res)

    trades, max_dd, end_eq = run_daily_campaign(d, sig, gate, atr, regime_d, dir_d, wan_d)

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
    by_side: dict[str, list] = defaultdict(list)
    for t in trades:
        by_year[t["year"]].append(t)
        by_reg[t["regime"]].append(t)
        by_reason[t["exit_status"]] += 1
        by_side[t["side"]].append(t)

    last_d = d["time"][-1]
    end_year = int(str(last_d)[:4])
    yearly = [
        {"year": str(y), **bucket(by_year.get(str(y), []))}
        for y in range(2015, end_year + 1)
    ]
    n = len(trades)
    after_warm = np.arange(d["close"].size) >= WARMUP
    n_range = int(np.sum(gate & after_warm))
    n_after = int(np.sum(after_warm))
    raw_long = int(np.sum(sig["entry_long"]))
    raw_short = int(np.sum(sig["entry_short"]))
    gated_long = int(np.sum(sig["entry_long"] & gate))
    gated_short = int(np.sum(sig["entry_short"] & gate))
    summary = {
        "instrument": INSTRUMENT,
        "engine": "cycle_trade_lien_clock_d1",
        "note": (
            "Causal filt Kalman. Daily Murphy [10,20,40] composite-cycle turns; "
            "alignment min 2 of 3; Ch.7 fade_range gate. Not a Lien chapter."
        ),
        "window": f"{FROM_T} to {last_d}",
        "last_daily_bar": last_d,
        "fill_mode": "next_open",
        "ch7": "fade_range (regime=range, not waning); flatten on leave-range",
        "value_per_price_unit": VPPU,
        "starting_equity": START_EQUITY,
        "risk_fraction": RISK_FRAC,
        "atr": {"period": ATR_PERIOD, "k": ATR_K},
        "decision": {
            "cycle_periods_bars": SSM_KALMAN_PARAMS_LIEN_D1_CYCLE["cycle_periods_bars"],
            "use_alignment_filter": True,
            "alignment_min_cycles": ALIGNMENT_MIN,
            "use_phase_confirm": True,
            "use_regime_filter": False,
            "tp_half_cycle": True,
            "T_max": T_MAX,
            "warmup_bars": WARMUP,
        },
        "ssm": {k: SSM_KALMAN_PARAMS_LIEN_D1_CYCLE[k] for k in SSM_KALMAN_PARAMS_LIEN_D1_CYCLE},
        "n_daily": int(d["close"].size),
        "occupancy": {
            "fade_range_bars_after_warmup": n_range,
            "bars_after_warmup": n_after,
            "fade_range_frac": round(n_range / n_after, 4) if n_after else None,
        },
        "signals": {
            "raw_long": raw_long,
            "raw_short": raw_short,
            "gated_long": gated_long,
            "gated_short": gated_short,
        },
        "trade_count": n,
        "ending_equity": round(end_eq, 4),
        "max_drawdown_frac": round(max_dd, 4),
        **bucket(trades),
        "exits": dict(by_reason),
        "by_side": {side: bucket(rows) for side, rows in sorted(by_side.items())},
        "by_regime": {rg: bucket(rows) for rg, rows in sorted(by_reg.items())},
        "yearly": yearly,
        "sliced": True,
    }
    TRADES_PATH.write_text(json.dumps(trades, indent=2) + "\n")
    SLICES_PATH.write_text(json.dumps(slice_rows, indent=2) + "\n")
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: summary[k]
                for k in (
                    "instrument",
                    "window",
                    "trade_count",
                    "ending_equity",
                    "mean_r",
                    "win_rate",
                    "max_drawdown_frac",
                    "mean_duration_days",
                    "occupancy",
                    "signals",
                    "exits",
                    "by_side",
                    "by_regime",
                    "yearly",
                )
                if k in summary
            },
            indent=2,
        ),
        flush=True,
    )
    print(f"WROTE {SUMMARY_PATH}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
