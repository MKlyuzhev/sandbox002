"""Causal USD_JPY Lien-clock Kalman acceptance walk. Research only; no orders.

Daily SSM owns direction; H1 SSM is a dip timer with Q derived ``/24``.
KPIs: hold duration, daily SMA 10/20/50 agreement, H1 entries as pullbacks
inside a daily long/short. Does **not** search ``q_*`` on PnL.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

# ssm_lib imports matplotlib/pandas plot helpers at module level; the filter does not need them.
import types

_plot = types.ModuleType("plot_x_utils")
_plot.apply_gapless_time_ticks = lambda *a, **k: None
_plot.resolve_ssm_plot_x = lambda *a, **k: None
sys.modules.setdefault("plot_x_utils", _plot)
_mpl = types.ModuleType("matplotlib")
_plt = types.ModuleType("matplotlib.pyplot")
_mpl.pyplot = _plt
sys.modules.setdefault("matplotlib", _mpl)
sys.modules.setdefault("matplotlib.pyplot", _plt)

_S002 = Path(__file__).resolve().parents[2]
_S001 = Path("/home/maxim/Projects/sandbox001")
for _p in (
    _S002,
    _S001 / "SB_QuantAna",
    _S001 / "SB_QuantAna" / "libraries",
    _S001 / "SB_QuantAna" / "trend_trade",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import ssm_lib  # noqa: E402
from app import oanda_client  # noqa: E402
from trend_strategy_defaults import (  # noqa: E402
    LIEN_SMA_PERIODS,
    LienH1DipConfig,
    SSM_KALMAN_PARAMS_2015_H1,
    SSM_KALMAN_PARAMS_LIEN_D1,
    SSM_KALMAN_PARAMS_LIEN_D1_CYCLES,
    SSM_KALMAN_PARAMS_LIEN_H1_TIMING,
    SSM_KALMAN_PARAMS_LIEN_H1_TIMING_CYCLES,
    h1_dip_entry_masks,
    kalman_kwargs_from_ssm_params,
    pip_size_for_symbol,
)

OUT = Path(__file__).resolve().parent
FROM_T = "2015-01-01T00:00:00Z"
TO_T = "2026-09-10T00:00:00Z"
INSTRUMENT = "USD_JPY"
STEPS_PATH = OUT / "daily_ch7_steps.json"
DAILY_CACHE = OUT / "usd_jpy_d_ohlc.json"
H1_CACHE = OUT / "usd_jpy_h1_ohlc.json"
SUMMARY_PATH = OUT / "kalman_lien_clock.json"
PIP = pip_size_for_symbol(INSTRUMENT)


def _parse_ts(value: str) -> datetime:
    text = str(value).replace("Z", "+00:00")
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


def _unix(value: str) -> float:
    return _parse_ts(value).timestamp()


def sma(x: np.ndarray, n: int) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    out = np.full(x.shape, np.nan, dtype=float)
    if x.size < n:
        return out
    c = np.cumsum(x, dtype=float)
    out[n - 1 :] = (c[n - 1 :] - np.concatenate(([0.0], c[:-n]))) / float(n)
    return out


def stack_sign(close: np.ndarray) -> np.ndarray:
    s10, s20, s50 = (sma(close, n) for n in (10, 20, 50))
    up = (s10 > s20) & (s20 > s50)
    down = (s10 < s20) & (s20 < s50)
    out = np.zeros(close.size, dtype=float)
    out[up] = 1.0
    out[down] = -1.0
    out[~np.isfinite(s50)] = np.nan
    return out


def slope_z_from_fit(res: dict) -> np.ndarray:
    slope = np.asarray(res["components_filt"]["slope"], dtype=float)
    var = np.asarray(res["P_filt"][:, 1, 1], dtype=float)
    return slope / np.sqrt(np.maximum(var, 1e-18))


def fit_slope_z(close: np.ndarray, ssm: dict) -> np.ndarray:
    y = np.log(np.asarray(close, dtype=float))
    kwargs = kalman_kwargs_from_ssm_params(ssm)
    kwargs["do_smooth"] = False
    res = ssm_lib.fit_price_components_kalman(y=y, dt=1.0, **kwargs)
    return slope_z_from_fit(res)


def align_asof_float(higher_t: np.ndarray, higher_v: np.ndarray, lower_t: np.ndarray) -> np.ndarray:
    out = np.full(lower_t.shape, np.nan, dtype=float)
    j = 0
    last = np.nan
    n_h = higher_t.size
    hv = np.asarray(higher_v, dtype=float)
    for i, t in enumerate(lower_t):
        while j < n_h and higher_t[j] <= t:
            last = float(hv[j])
            j += 1
        out[i] = last
    return out


def align_asof_obj(higher_t: np.ndarray, higher_v: np.ndarray, lower_t: np.ndarray) -> np.ndarray:
    hv = list(higher_v)
    out: list[object] = []
    j = 0
    last: object = ""
    n_h = higher_t.size
    for t in lower_t:
        while j < n_h and higher_t[j] <= t:
            last = hv[j]
            j += 1
        out.append(last)
    return np.array(out, dtype=object)


def join_trend_masks(regime: np.ndarray, direction: np.ndarray, waning: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    trend = regime == "trend"
    long_ok = trend & (~waning) & (direction == "up")
    short_ok = trend & (~waning) & (direction == "down")
    return long_ok, short_ok


def run_daily_backtest(
    close: np.ndarray,
    opens: np.ndarray,
    slope_z: np.ndarray,
    *,
    z_entry: float,
    z_exit: float,
    t_max: int,
    warmup: int,
    long_gate: np.ndarray | None = None,
    short_gate: np.ndarray | None = None,
) -> list[dict]:
    n = close.size
    trades: list[dict] = []
    pos = 0
    entry_i = -1
    entry_px = 0.0
    for i in range(n - 1):
        if i < warmup:
            continue
        z = float(slope_z[i])
        if not np.isfinite(z):
            continue
        long_ok = True if long_gate is None else bool(long_gate[i])
        short_ok = True if short_gate is None else bool(short_gate[i])
        fill = i + 1
        if pos == 0:
            if z > z_entry and long_ok:
                pos, entry_i, entry_px = 1, fill, float(opens[fill])
            elif z < -z_entry and short_ok:
                pos, entry_i, entry_px = -1, fill, float(opens[fill])
            continue
        held = fill - entry_i
        weak = z < z_exit if pos == 1 else z > -z_exit
        gate_off = (not long_ok) if pos == 1 else (not short_ok)
        timed = held >= t_max
        if not (weak or gate_off or timed):
            continue
        exit_px = float(opens[fill])
        pnl = (exit_px - entry_px) * pos
        reason = "TIME_stop" if timed and not weak else ("gate" if gate_off and not weak else "EXIT_slope_weak")
        trades.append(
            {
                "side": "long" if pos == 1 else "short",
                "entry_i": entry_i,
                "exit_i": fill,
                "duration_bars": held,
                "pnl_pips": pnl / PIP,
                "reason": reason,
                "entry_z": float(slope_z[entry_i - 1]) if entry_i > 0 else z,
            }
        )
        pos = 0
    return trades


def run_dual_backtest(
    h1_open: np.ndarray,
    h1_z: np.ndarray,
    daily_z_al: np.ndarray,
    long_pulse: np.ndarray,
    short_pulse: np.ndarray,
    long_gate: np.ndarray,
    short_gate: np.ndarray,
    *,
    z_exit: float,
) -> list[dict]:
    n = h1_open.size
    trades: list[dict] = []
    pos = 0
    entry_i = -1
    entry_px = 0.0
    entry_h1_z = 0.0
    entry_daily_z = 0.0
    for i in range(n - 1):
        fill = i + 1
        dz = float(daily_z_al[i])
        if pos == 0:
            if bool(long_pulse[i]) and bool(long_gate[i]) and np.isfinite(dz):
                pos, entry_i, entry_px = 1, fill, float(h1_open[fill])
                entry_h1_z = float(h1_z[i])
                entry_daily_z = dz
            elif bool(short_pulse[i]) and bool(short_gate[i]) and np.isfinite(dz):
                pos, entry_i, entry_px = -1, fill, float(h1_open[fill])
                entry_h1_z = float(h1_z[i])
                entry_daily_z = dz
            continue
        weak = dz < z_exit if pos == 1 else dz > -z_exit
        gate_off = (not bool(long_gate[i])) if pos == 1 else (not bool(short_gate[i]))
        if not (weak or gate_off):
            continue
        exit_px = float(h1_open[fill])
        pnl = (exit_px - entry_px) * pos
        trades.append(
            {
                "side": "long" if pos == 1 else "short",
                "entry_i": entry_i,
                "exit_i": fill,
                "duration_bars": fill - entry_i,
                "duration_days": (fill - entry_i) / 24.0,
                "pnl_pips": pnl / PIP,
                "reason": "gate" if gate_off and not weak else "EXIT_daily_slope_weak",
                "entry_h1_z": entry_h1_z,
                "entry_daily_z": entry_daily_z,
            }
        )
        pos = 0
    return trades


def trade_stats(trades: list[dict], duration_key: str = "duration_bars") -> dict:
    n = len(trades)
    if not n:
        return {"n": 0}
    durs = [float(t[duration_key]) for t in trades]
    pips = [float(t["pnl_pips"]) for t in trades]
    wins = sum(1 for x in pips if x > 0)
    return {
        "n": n,
        "mean_duration": round(sum(durs) / n, 3),
        "median_duration": round(float(np.median(durs)), 3),
        "mean_pnl_pips": round(sum(pips) / n, 2),
        "sum_pnl_pips": round(sum(pips), 2),
        "win_rate": round(wins / n, 4),
    }


def stack_agreement(slope_z: np.ndarray, stack: np.ndarray, warmup: int) -> dict:
    z = np.asarray(slope_z, dtype=float)
    s = np.asarray(stack, dtype=float)
    ok = np.isfinite(z) & np.isfinite(s) & (s != 0) & (np.arange(z.size) >= warmup)
    if not np.any(ok):
        return {"n": 0}
    agree = np.sign(z[ok]) == np.sign(s[ok])
    return {
        "n": int(ok.sum()),
        "frac": round(float(agree.mean()), 4),
    }


async def load_ohlc(granularity: str, cache: Path) -> list[dict]:
    if cache.exists():
        print(f"SKIP fetch {granularity} ({cache.name})", flush=True)
        return json.loads(cache.read_text())
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


def arrays_from_bars(bars: list[dict]) -> dict[str, np.ndarray]:
    return {
        "t": np.array([_unix(b["time"]) for b in bars], dtype=float),
        "open": np.array([b["open"] for b in bars], dtype=float),
        "close": np.array([b["close"] for b in bars], dtype=float),
        "time": [b["time"] for b in bars],
    }


def align_ch7(daily_t: np.ndarray, steps: list[dict]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    st = np.array([_unix(s["time"]) for s in steps], dtype=float)
    regime = np.array([s.get("regime") or "?" for s in steps], dtype=object)
    direction = np.array([s.get("direction") or "?" for s in steps], dtype=object)
    waning = np.array([bool(s.get("trend_waning")) for s in steps], dtype=bool)
    return (
        align_asof_obj(st, regime, daily_t),
        align_asof_obj(st, direction, daily_t),
        align_asof_float(st, waning.astype(float), daily_t) > 0.5,
    )


def pullback_kpis(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0}
    same_sign = 0
    smaller_h1 = 0
    for t in trades:
        hz = float(t["entry_h1_z"])
        dz = float(t["entry_daily_z"])
        if hz == 0 or dz == 0 or not np.isfinite(hz) or not np.isfinite(dz):
            continue
        if np.sign(hz) == np.sign(dz):
            same_sign += 1
        if abs(hz) < abs(dz):
            smaller_h1 += 1
    n = len(trades)
    return {
        "n": n,
        "frac_same_sign_as_daily": round(same_sign / n, 4),
        "frac_abs_h1_z_lt_abs_daily_z": round(smaller_h1 / n, 4),
        "mean_abs_h1_z": round(float(np.mean([abs(t["entry_h1_z"]) for t in trades])), 4),
        "mean_abs_daily_z": round(float(np.mean([abs(t["entry_daily_z"]) for t in trades])), 4),
    }


async def main() -> None:
    steps = json.loads(STEPS_PATH.read_text())
    daily_bars = await load_ohlc("D", DAILY_CACHE)
    h1_bars = await load_ohlc("H1", H1_CACHE)
    d = arrays_from_bars(daily_bars)
    h = arrays_from_bars(h1_bars)
    print(f"bars daily={d['close'].size} h1={h['close'].size}", flush=True)

    regime_d, dir_d, wan_d = align_ch7(d["t"], steps)
    long_g_d, short_g_d = join_trend_masks(regime_d, dir_d, wan_d)

    print("FIT daily freeze / LLT / cycles", flush=True)
    z_freeze = fit_slope_z(d["close"], SSM_KALMAN_PARAMS_2015_H1)
    z_llt = fit_slope_z(d["close"], SSM_KALMAN_PARAMS_LIEN_D1)
    z_cyc = fit_slope_z(d["close"], SSM_KALMAN_PARAMS_LIEN_D1_CYCLES)
    stack = stack_sign(d["close"])

    variants: dict[str, dict] = {}

    freeze_trades = run_daily_backtest(
        d["close"],
        d["open"],
        z_freeze,
        z_entry=0.05,
        z_exit=0.01,
        t_max=100,
        warmup=50,
    )
    variants["freeze_2015_on_daily"] = {
        "note": "Historical H1 Q and z_entry=0.05 on the daily series (wrong clock).",
        "trades": trade_stats(freeze_trades),
        "sma_stack_agreement": stack_agreement(z_freeze, stack, 50),
    }

    llt_ungated = run_daily_backtest(
        d["close"],
        d["open"],
        z_llt,
        z_entry=1.0,
        z_exit=0.25,
        t_max=60,
        warmup=200,
    )
    variants["lien_d1_llt_ungated"] = {
        "trades": trade_stats(llt_ungated),
        "sma_stack_agreement": stack_agreement(z_llt, stack, 200),
        "mean_duration_days": trade_stats(llt_ungated)["mean_duration"]
        if llt_ungated
        else None,
    }

    llt_ch7 = run_daily_backtest(
        d["close"],
        d["open"],
        z_llt,
        z_entry=1.0,
        z_exit=0.25,
        t_max=60,
        warmup=200,
        long_gate=long_g_d,
        short_gate=short_g_d,
    )
    variants["lien_d1_llt_ch7"] = {
        "trades": trade_stats(llt_ch7),
        "sma_stack_agreement": stack_agreement(z_llt, stack, 200),
    }

    cyc_ch7 = run_daily_backtest(
        d["close"],
        d["open"],
        z_cyc,
        z_entry=1.0,
        z_exit=0.25,
        t_max=60,
        warmup=200,
        long_gate=long_g_d,
        short_gate=short_g_d,
    )
    variants["lien_d1_cycles_ch7"] = {
        "trades": trade_stats(cyc_ch7),
        "sma_stack_agreement": stack_agreement(z_cyc, stack, 200),
    }

    print("FIT H1 timing LLT / cycles", flush=True)
    z_h1 = fit_slope_z(h["close"], SSM_KALMAN_PARAMS_LIEN_H1_TIMING)
    z_h1_c = fit_slope_z(h["close"], SSM_KALMAN_PARAMS_LIEN_H1_TIMING_CYCLES)
    daily_z_h1 = align_asof_float(d["t"], z_llt, h["t"])
    long_g_h = align_asof_float(d["t"], long_g_d.astype(float), h["t"]) > 0.5
    short_g_h = align_asof_float(d["t"], short_g_d.astype(float), h["t"]) > 0.5
    dip_cfg = LienH1DipConfig(z_entry_daily=1.0, z_dip=0.25, warmup_bars=50)

    long_p, short_p = h1_dip_entry_masks(daily_z_h1, z_h1, dip_cfg)
    dual = run_dual_backtest(
        h["open"],
        z_h1,
        daily_z_h1,
        long_p,
        short_p,
        long_g_h,
        short_g_h,
        z_exit=0.25,
    )
    dual_days = [{**t, "duration_bars": t["duration_days"]} for t in dual]
    variants["dual_llt_ch7"] = {
        "trades_h1_bars": trade_stats(dual),
        "trades_days": trade_stats(dual_days),
        "pullback": pullback_kpis(dual),
        "daily_sma_stack_agreement": stack_agreement(z_llt, stack, 200),
    }

    long_pc, short_pc = h1_dip_entry_masks(daily_z_h1, z_h1_c, dip_cfg)
    dual_c = run_dual_backtest(
        h["open"],
        z_h1_c,
        daily_z_h1,
        long_pc,
        short_pc,
        long_g_h,
        short_g_h,
        z_exit=0.25,
    )
    dual_c_days = [{**t, "duration_bars": t["duration_days"]} for t in dual_c]
    variants["dual_h1_cycles_ch7"] = {
        "trades_h1_bars": trade_stats(dual_c),
        "trades_days": trade_stats(dual_c_days),
        "pullback": pullback_kpis(dual_c),
    }

    occupancy = defaultdict(int)
    for rg, w in zip(regime_d, wan_d):
        occupancy[str(rg)] += 1
        if w:
            occupancy["waning"] += 1

    pull = variants["dual_llt_ch7"]["pullback"]
    freeze_d = variants["freeze_2015_on_daily"]["trades"].get("mean_duration", 0)
    llt_d = variants["lien_d1_llt_ungated"]["trades"].get("mean_duration", 0)
    cyc_d = variants["lien_d1_cycles_ch7"]["trades"].get("mean_duration", 0)
    fr_ag = variants["freeze_2015_on_daily"]["sma_stack_agreement"].get("frac", 0)
    llt_ag = variants["lien_d1_llt_ungated"]["sma_stack_agreement"].get("frac", 0)
    cyc_ag = variants["lien_d1_cycles_ch7"]["sma_stack_agreement"].get("frac", 0)
    acceptance = {
        "daily_holds_longer_than_freeze": bool(llt_d > freeze_d),
        "weeks_scale_mean_ge_10_llt": bool(llt_d >= 10),
        "weeks_scale_mean_ge_10_cycles": bool(cyc_d >= 10),
        "stack_agreement_better_than_freeze_llt": bool(llt_ag > fr_ag),
        "stack_agreement_better_than_freeze_cycles": bool(cyc_ag > fr_ag),
        "h1_entries_same_sign_as_daily": bool(pull.get("frac_same_sign_as_daily", 0) >= 0.9),
        "h1_abs_z_lt_daily": bool(pull.get("frac_abs_h1_z_lt_abs_daily_z", 0) >= 0.5),
        "q_not_searched_on_pnl": True,
    }

    report = {
        "instrument": INSTRUMENT,
        "from": FROM_T,
        "to": TO_T,
        "pip_size": PIP,
        "n_daily": int(d["close"].size),
        "n_h1": int(h["close"].size),
        "ch7_occupancy_daily_bars": dict(occupancy),
        "priors": {
            "lien_d1": {k: SSM_KALMAN_PARAMS_LIEN_D1[k] for k in SSM_KALMAN_PARAMS_LIEN_D1},
            "lien_h1_timing_q_slope": SSM_KALMAN_PARAMS_LIEN_H1_TIMING["q_slope"],
            "lien_sma_periods": list(LIEN_SMA_PERIODS),
        },
        "kpi_note": (
            "PnL is reported, not used to pick Q. Pass if daily holds are weeks, "
            "slope_z tracks SMA 10/20/50 better than the 2015 freeze, and H1 "
            "entries match daily sign with smaller |h1_z|."
        ),
        "acceptance": acceptance,
        "variants": variants,
    }
    SUMMARY_PATH.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({"acceptance": acceptance, "variants": report["variants"]}, indent=2, default=str), flush=True)
    print(f"WROTE {SUMMARY_PATH}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
