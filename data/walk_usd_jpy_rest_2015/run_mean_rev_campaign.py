"""Causal mean_reversion_trade campaign: USD_JPY 2015-now, daily Murphy 10/20/40.

Research only; no orders. Kalman Q is the frozen Lien-clock mean-rev prior
(not PnL-searched). Stretch is Chan ±2σ; hold cap is Chan half-life 10 days.
Ch.7 ``fade_range`` (regime range, not waning) gates entries and flattens on
leave-range. ATR 14×3 sizes 2% equity. Residual 2σ is an entry, not a stop.
Half-year slices match the rest-fill / cycle_trade campaign windowing.
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
_mrev = _S001 / "SB_QuantAna" / "mean_reversion_trade"
if str(_mrev) not in sys.path:
    sys.path.insert(0, str(_mrev))

import run_kalman_lien_clock as kalman  # noqa: E402
import ssm_lib  # noqa: E402
from mean_rev_strategy_defaults import (  # noqa: E402
    SSM_KALMAN_PARAMS_LIEN_D1_MEANREV,
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
T_MAX = 10
WARMUP = 80
K_ENTRY = 2.0
TP_ABS_EPS_SIGMA = 1.0
TP_RESID_CLOSE = 0.0
EPS_LONG_FRAC = 0.25
EPS_WINDOW = 80
PHASE_LONG = (-np.pi, -0.5 * np.pi)
PHASE_SHORT = (0.5 * np.pi, np.pi)

TRADES_PATH = OUT / "mean_rev_trades.json"
SLICES_PATH = OUT / "mean_rev_slices.json"
SUMMARY_PATH = OUT / "mean_rev_campaign.json"


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


def rolling_std(x: np.ndarray, window: int) -> np.ndarray:
    n = x.size
    out = np.full(n, np.nan)
    minp = max(20, window // 4)
    for i in range(minp - 1, n):
        sl = x[max(0, i - window + 1) : i + 1]
        if sl.size > 1:
            out[i] = float(np.std(sl, ddof=1))
    return out


def expanding_std_shift1(x: np.ndarray, min_periods: int) -> np.ndarray:
    """Causal expanding sample std: bar i uses x[:i] (ddof=1)."""
    n = x.size
    out = np.full(n, np.nan)
    csum = 0.0
    csum2 = 0.0
    count = 0
    minp = max(2, int(min_periods))
    for i in range(n):
        if count >= minp:
            mean = csum / count
            var = (csum2 - count * mean * mean) / (count - 1)
            out[i] = float(np.sqrt(max(var, 0.0)))
        v = float(x[i])
        if np.isfinite(v):
            csum += v
            csum2 += v * v
            count += 1
    return out


def mean_rev_signals(y: np.ndarray, res: dict) -> dict[str, np.ndarray]:
    """Numpy port of trade_res_mean_rev.compute_signals for the Lien-d1 book prior.

    stretch_mode=sigma, k_entry=2, A_min=0, Kalman range off. Ch.7 sits outside.
    Does not roll the entry mask: the campaign fills next open (bar i+1).
    """
    comps = res["components_filt"]
    recon = np.asarray(comps["recon"], dtype=float)
    c0 = np.asarray(comps["cycle_0_c"], dtype=float)
    s0 = np.asarray(comps["cycle_0_s"], dtype=float)
    c1 = np.asarray(comps["cycle_1_c"], dtype=float)
    c2 = np.asarray(comps["cycle_2_c"], dtype=float)
    t = y.size
    resid = y - recon
    sigma = expanding_std_shift1(resid, WARMUP)
    z_resid = resid / np.maximum(sigma, 1e-12)
    phase0 = np.arctan2(c0, s0)
    c2_std = rolling_std(c2, EPS_WINDOW)
    eps = EPS_LONG_FRAC * np.where(np.isfinite(c2_std) & (c2_std > 0), c2_std, np.nan)
    c2_neutral = np.abs(c2) < eps
    long_allowed = (c1 < 0) & ((c2 <= 0) | c2_neutral)
    short_allowed = (c1 > 0) & ((c2 >= 0) | c2_neutral)
    long_timing = (phase0 >= PHASE_LONG[0]) & (phase0 <= PHASE_LONG[1])
    short_timing = (phase0 >= PHASE_SHORT[0]) & (phase0 <= PHASE_SHORT[1])
    long_stretch = z_resid < -K_ENTRY
    short_stretch = z_resid > K_ENTRY
    entry_long = long_allowed & long_timing & long_stretch
    entry_short = short_allowed & short_timing & short_stretch
    if WARMUP > 0:
        w_bars = min(WARMUP, t)
        entry_long = np.asarray(entry_long, dtype=bool).copy()
        entry_short = np.asarray(entry_short, dtype=bool).copy()
        entry_long[:w_bars] = False
        entry_short[:w_bars] = False
    exit_long_tp = (resid >= TP_RESID_CLOSE) | (np.abs(resid) <= TP_ABS_EPS_SIGMA * np.maximum(sigma, 0.0))
    exit_short_tp = (resid <= -TP_RESID_CLOSE) | (np.abs(resid) <= TP_ABS_EPS_SIGMA * np.maximum(sigma, 0.0))
    return {
        "entry_long": entry_long.astype(bool),
        "entry_short": entry_short.astype(bool),
        "exit_long_tp": exit_long_tp.astype(bool),
        "exit_short_tp": exit_short_tp.astype(bool),
        "long_stretch": long_stretch.astype(bool),
        "short_stretch": short_stretch.astype(bool),
        "long_allowed": long_allowed.astype(bool),
        "short_allowed": short_allowed.astype(bool),
        "long_timing": long_timing.astype(bool),
        "short_timing": short_timing.astype(bool),
        "z_resid": z_resid,
        "resid": resid,
        "sigma": sigma,
        "phase0": phase0,
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
    entry_phase = 0.0
    entry_regime = ""
    entry_dir = ""
    entry_wan = False

    def _open(side: int, i: int, fill: int) -> None:
        nonlocal pos, entry_i, entry_px, stop_px, risk_cash, units
        nonlocal entry_atr, entry_z, entry_phase, entry_regime, entry_dir, entry_wan
        atr_i = float(atr[i])
        dist = ATR_K * atr_i
        pos = side
        entry_i = fill
        entry_px = float(d["open"][fill])
        stop_px = entry_px - pos * dist
        risk_cash = RISK_FRAC * equity
        units = risk_cash / max(dist * VPPU, 1e-12)
        entry_atr = atr_i
        entry_z = float(sig["z_resid"][i])
        entry_phase = float(sig["phase0"][i])
        entry_regime = str(regime[i])
        entry_dir = str(direction[i])
        entry_wan = bool(waning[i])

    for i in range(n - 1):
        fill = i + 1
        if pos == 0:
            atr_i = float(atr[i])
            if not np.isfinite(atr_i) or atr_i <= 0:
                continue
            if not bool(gate[i]):
                continue
            if bool(sig["entry_long"][i]):
                _open(1, i, fill)
            elif bool(sig["entry_short"][i]):
                _open(-1, i, fill)
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
            reason = "TP_z_le_1"
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
                "entry_z_resid": round(entry_z, 4),
                "entry_phase0": round(entry_phase, 4),
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
                _open(-1 if closed_side == 1 else 1, i, fill)
    return trades, max_dd, equity


def bucket(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    rs = [float(t["r"]) for t in rows]
    wins = sum(1 for r in rs if r > 0)
    pips = [float(t["pnl_pips"]) for t in rows]
    durs = [float(t["duration_days"]) for t in rows]
    zs = [float(t["entry_z_resid"]) for t in rows]
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
        "mean_entry_abs_z": round(float(np.mean(np.abs(zs))), 3),
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

    print("FIT daily mean-rev SSM [10,20,40] (causal filt)", flush=True)
    y = np.log(np.asarray(d["close"], dtype=float))
    kwargs = kalman_kwargs_from_ssm_params(SSM_KALMAN_PARAMS_LIEN_D1_MEANREV)
    kwargs["do_smooth"] = False
    res = ssm_lib.fit_price_components_kalman(y=y, dt=1.0, **kwargs)
    sig = mean_rev_signals(y, res)

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
    stretch_long = sig["long_stretch"] & after_warm
    stretch_short = sig["short_stretch"] & after_warm
    stretch = stretch_long | stretch_short
    phase = (sig["long_timing"] & stretch_long) | (sig["short_timing"] & stretch_short)
    allowed = (sig["long_allowed"] & stretch_long) | (sig["short_allowed"] & stretch_short)
    raw_long = int(np.sum(sig["entry_long"]))
    raw_short = int(np.sum(sig["entry_short"]))
    gated_long = int(np.sum(sig["entry_long"] & gate))
    gated_short = int(np.sum(sig["entry_short"] & gate))
    peak_eq = max([START_EQUITY] + [float(t["equity"]) for t in trades])
    summary = {
        "instrument": INSTRUMENT,
        "engine": "mean_rev_lien_clock_d1",
        "note": (
            "Causal filt Kalman. Daily Murphy [10,20,40] residual z vs recon; "
            "Chan k_entry=2 / TP |z|<=1 / T_max=10; Ch.7 fade_range gate. "
            "Not a Lien chapter."
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
            "cycle_periods_bars": SSM_KALMAN_PARAMS_LIEN_D1_MEANREV["cycle_periods_bars"],
            "stretch_mode": "sigma",
            "k_entry": K_ENTRY,
            "tp_resid_close": TP_RESID_CLOSE,
            "tp_abs_eps_sigma": TP_ABS_EPS_SIGMA,
            "use_resid_stop": False,
            "use_range_regime": False,
            "use_slope_z": False,
            "A_min": 0.0,
            "T_max": T_MAX,
            "warmup_bars": WARMUP,
            "phase_c1_c2": True,
        },
        "ssm": {k: SSM_KALMAN_PARAMS_LIEN_D1_MEANREV[k] for k in SSM_KALMAN_PARAMS_LIEN_D1_MEANREV},
        "n_daily": int(d["close"].size),
        "occupancy": {
            "fade_range_bars_after_warmup": n_range,
            "bars_after_warmup": n_after,
            "fade_range_frac": round(n_range / n_after, 4) if n_after else None,
            "stretch_bars": int(np.sum(stretch)),
            "stretch_long_bars": int(np.sum(stretch_long)),
            "stretch_short_bars": int(np.sum(stretch_short)),
            "stretch_and_fade": int(np.sum(stretch & gate)),
            "stretch_and_fade_long": int(np.sum(stretch_long & gate)),
            "stretch_and_fade_short": int(np.sum(stretch_short & gate)),
            "stretch_fade_and_phase": int(np.sum(phase & gate)),
            "stretch_fade_and_c1c2": int(np.sum(allowed & gate)),
            "stretch_fade_phase_c1c2": gated_long + gated_short,
        },
        "signals": {
            "raw_long": raw_long,
            "raw_short": raw_short,
            "gated_long": gated_long,
            "gated_short": gated_short,
        },
        "trade_count": n,
        "ending_equity": round(end_eq, 4),
        "peak_equity": round(peak_eq, 4),
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
                    "peak_equity",
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
