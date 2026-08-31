"""Deterministic OHLC indicators for the Lien Ch. 7 governing layer.

Pure functions only: no LLM, no network, no I/O. Bars in; JSON-friendly
dicts out. The model chooses *which* snapshot to request; the numbers are
computed here (see docs/LIEN_FX_STRATEGIES.md).
"""

from __future__ import annotations

from typing import Any

SMA_PERIODS: tuple[int, ...] = (10, 20, 50, 100, 200)
BB_PERIOD = 20
ADX_PERIOD = 14
ADX_SLOPE_BARS = 5
RSI_PERIOD = 14
STOCH_K = 14
STOCH_SMOOTH = 3
STOCH_D = 3
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
HIGH_N = 10
MIN_BARS = 30


class IndicatorError(ValueError):
    """Raised when inputs violate an indicator invariant."""


def _validate_bars(bars: list[dict]) -> None:
    if not bars:
        raise IndicatorError("bars must be non-empty")
    for i, b in enumerate(bars):
        for key in ("open", "high", "low", "close"):
            if key not in b:
                raise IndicatorError(f"bar[{i}] missing '{key}'")
        if b["high"] < b["low"]:
            raise IndicatorError(f"bar[{i}] high < low")


def _closes(bars: list[dict]) -> list[float]:
    return [float(b["close"]) for b in bars]


def _highs(bars: list[dict]) -> list[float]:
    return [float(b["high"]) for b in bars]


def _lows(bars: list[dict]) -> list[float]:
    return [float(b["low"]) for b in bars]


def _round(value: float | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _last(series: list[float | None]) -> float | None:
    if not series:
        return None
    return series[-1]


def sma_series(values: list[float], period: int) -> list[float | None]:
    """Simple moving average; ``None`` until ``period`` values exist."""
    if period < 1:
        raise IndicatorError("period must be >= 1")
    n = len(values)
    out: list[float | None] = [None] * n
    if n < period:
        return out
    window = 0.0
    for i, v in enumerate(values):
        window += v
        if i >= period:
            window -= values[i - period]
        if i >= period - 1:
            out[i] = window / period
    return out


def sma_of_optional(values: list[float | None], period: int) -> list[float | None]:
    """SMA over a series that may contain leading ``None``s (skips until dense)."""
    n = len(values)
    out: list[float | None] = [None] * n
    if period < 1:
        raise IndicatorError("period must be >= 1")
    buf: list[float] = []
    for i, v in enumerate(values):
        if v is None:
            buf = []
            continue
        buf.append(v)
        if len(buf) > period:
            buf.pop(0)
        if len(buf) == period:
            out[i] = sum(buf) / period
    return out


def stdev_series(values: list[float], period: int) -> list[float | None]:
    """Population standard deviation over a rolling window (Bollinger convention)."""
    if period < 2:
        raise IndicatorError("stdev period must be >= 2")
    n = len(values)
    out: list[float | None] = [None] * n
    if n < period:
        return out
    for i in range(period - 1, n):
        window = values[i - period + 1 : i + 1]
        mean = sum(window) / period
        var = sum((x - mean) ** 2 for x in window) / period
        out[i] = var**0.5
    return out


def ema_series(values: list[float], period: int) -> list[float | None]:
    """EMA seeded with SMA of the first ``period`` values."""
    if period < 1:
        raise IndicatorError("period must be >= 1")
    n = len(values)
    out: list[float | None] = [None] * n
    if n < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    k = 2.0 / (period + 1)
    for i in range(period, n):
        prev = out[i - 1]
        assert prev is not None
        out[i] = values[i] * k + prev * (1.0 - k)
    return out


def bollinger_series(
    closes: list[float],
    period: int = BB_PERIOD,
) -> dict[str, list[float | None]]:
    """20-period mid + 1σ and 2σ bands (Lien double Bollinger)."""
    mid = sma_series(closes, period)
    sd = stdev_series(closes, period)
    n = len(closes)
    upper_2: list[float | None] = [None] * n
    upper_1: list[float | None] = [None] * n
    lower_1: list[float | None] = [None] * n
    lower_2: list[float | None] = [None] * n
    for i in range(n):
        m, s = mid[i], sd[i]
        if m is None or s is None:
            continue
        upper_2[i] = m + 2.0 * s
        upper_1[i] = m + 1.0 * s
        lower_1[i] = m - 1.0 * s
        lower_2[i] = m - 2.0 * s
    return {
        "mid": mid,
        "upper_2": upper_2,
        "upper_1": upper_1,
        "lower_1": lower_1,
        "lower_2": lower_2,
        "stdev": sd,
    }


def bb_zone(close: float, bands: dict[str, float | None]) -> str:
    """Lien zone: outer 1σ–2σ (or beyond) is trend; between 1σ bands is range."""
    u1, u2 = bands.get("upper_1"), bands.get("upper_2")
    l1, l2 = bands.get("lower_1"), bands.get("lower_2")
    if None in (u1, u2, l1, l2):
        return "unknown"
    # Strict 1σ bounds: on the band or inside is range (Lien's inner zone).
    # Zero-width bands (flat closes) also count as range.
    if u1 - l1 <= 1e-12:  # type: ignore[operator]
        return "range"
    if close > u1:  # type: ignore[operator]
        return "trend_up"
    if close < l1:  # type: ignore[operator]
        return "trend_down"
    return "range"


def double_bollinger_state(
    closes: list[float],
    period: int = BB_PERIOD,
) -> dict[str, Any]:
    """Ch.9 double-Bollinger context: last-bar bands plus the last three zones.

    Lien's Ch.9 keys everything off a close crossing the 1σ band, and the join /
    fade distinction needs the prior bars' positions relative to that band. This
    returns the last bar's 1σ/2σ bands and the ``bb_zone`` of the last three
    bars (``zone`` newest, then ``prev_zone``, ``prev2_zone``) so an engine can
    detect the cross without recomputing indicators.
    """
    bb = bollinger_series(closes, period)
    n = len(closes)

    def _zone_at(i: int) -> str:
        if i < 0 or i >= n:
            return "unknown"
        bands = {
            "upper_1": bb["upper_1"][i],
            "upper_2": bb["upper_2"][i],
            "lower_1": bb["lower_1"][i],
            "lower_2": bb["lower_2"][i],
        }
        return bb_zone(closes[i], bands)

    return {
        "period": period,
        "upper_2": _round(_last(bb["upper_2"])),
        "upper_1": _round(_last(bb["upper_1"])),
        "mid": _round(_last(bb["mid"])),
        "lower_1": _round(_last(bb["lower_1"])),
        "lower_2": _round(_last(bb["lower_2"])),
        "zone": _zone_at(n - 1),
        "prev_zone": _zone_at(n - 2),
        "prev2_zone": _zone_at(n - 3),
    }


def adx_series(
    bars: list[dict],
    period: int = ADX_PERIOD,
) -> dict[str, list[float | None]]:
    """Wilder ADX / +DI / −DI."""
    _validate_bars(bars)
    n = len(bars)
    adx: list[float | None] = [None] * n
    plus_di: list[float | None] = [None] * n
    minus_di: list[float | None] = [None] * n
    dx: list[float | None] = [None] * n
    if n < period + 1:
        return {"adx": adx, "plus_di": plus_di, "minus_di": minus_di, "dx": dx}

    highs, lows, closes = _highs(bars), _lows(bars), _closes(bars)
    tr = [0.0] * n
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        h, l, pc = highs[i], lows[i], closes[i - 1]
        tr[i] = max(h - l, abs(h - pc), abs(l - pc))
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm[i] = up if up > down and up > 0 else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0

    atr = [None] * n  # type: list[float | None]
    sm_plus = [None] * n  # type: list[float | None]
    sm_minus = [None] * n  # type: list[float | None]
    atr[period] = sum(tr[1 : period + 1]) / period
    sm_plus[period] = sum(plus_dm[1 : period + 1]) / period
    sm_minus[period] = sum(minus_dm[1 : period + 1]) / period

    def _di(smoothed: float, atr_v: float) -> float:
        if atr_v == 0:
            return 0.0
        return 100.0 * smoothed / atr_v

    def _dx(pdi: float, mdi: float) -> float:
        denom = pdi + mdi
        if denom == 0:
            return 0.0
        return 100.0 * abs(pdi - mdi) / denom

    plus_di[period] = _di(sm_plus[period], atr[period])
    minus_di[period] = _di(sm_minus[period], atr[period])
    dx[period] = _dx(plus_di[period], minus_di[period])

    for i in range(period + 1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period  # type: ignore[operator]
        sm_plus[i] = (sm_plus[i - 1] * (period - 1) + plus_dm[i]) / period  # type: ignore[operator]
        sm_minus[i] = (sm_minus[i - 1] * (period - 1) + minus_dm[i]) / period  # type: ignore[operator]
        plus_di[i] = _di(sm_plus[i], atr[i])  # type: ignore[arg-type]
        minus_di[i] = _di(sm_minus[i], atr[i])  # type: ignore[arg-type]
        dx[i] = _dx(plus_di[i], minus_di[i])  # type: ignore[arg-type]

    # First ADX = SMA of the first ``period`` DX values (index period .. 2*period-1).
    first_adx = 2 * period - 1
    if n > first_adx:
        chunk = dx[period : first_adx + 1]
        if all(v is not None for v in chunk):
            adx[first_adx] = sum(chunk) / period  # type: ignore[arg-type]
            for i in range(first_adx + 1, n):
                prev = adx[i - 1]
                cur = dx[i]
                if prev is None or cur is None:
                    continue
                adx[i] = (prev * (period - 1) + cur) / period

    return {"adx": adx, "plus_di": plus_di, "minus_di": minus_di, "dx": dx}


def rsi_series(closes: list[float], period: int = RSI_PERIOD) -> list[float | None]:
    """Wilder RSI."""
    n = len(closes)
    out: list[float | None] = [None] * n
    if n <= period:
        return out

    def _rsi(avg_gain: float, avg_loss: float) -> float:
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
        if avg_gain == 0:
            return 0.0
        rs = avg_gain / avg_loss
        return 100.0 - 100.0 / (1.0 + rs)

    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        ch = closes[i] - closes[i - 1]
        gains += max(ch, 0.0)
        losses += max(-ch, 0.0)
    avg_g = gains / period
    avg_l = losses / period
    out[period] = _rsi(avg_g, avg_l)
    for i in range(period + 1, n):
        ch = closes[i] - closes[i - 1]
        avg_g = (avg_g * (period - 1) + max(ch, 0.0)) / period
        avg_l = (avg_l * (period - 1) + max(-ch, 0.0)) / period
        out[i] = _rsi(avg_g, avg_l)
    return out


def stoch_series(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    k_period: int = STOCH_K,
    k_smooth: int = STOCH_SMOOTH,
    d_period: int = STOCH_D,
) -> tuple[list[float | None], list[float | None]]:
    """Slow stochastic (14, 3, 3): smoothed %K and %D."""
    n = len(closes)
    raw_k: list[float | None] = [None] * n
    if n < k_period:
        return raw_k, list(raw_k)
    for i in range(k_period - 1, n):
        hh = max(highs[i - k_period + 1 : i + 1])
        ll = min(lows[i - k_period + 1 : i + 1])
        if hh == ll:
            raw_k[i] = 50.0
        else:
            raw_k[i] = 100.0 * (closes[i] - ll) / (hh - ll)
    slow_k = sma_of_optional(raw_k, k_smooth)
    slow_d = sma_of_optional(slow_k, d_period)
    return slow_k, slow_d


def macd_series(
    closes: list[float],
    fast: int = MACD_FAST,
    slow: int = MACD_SLOW,
    signal_period: int = MACD_SIGNAL,
) -> dict[str, list[float | None]]:
    """MACD line, signal, histogram (EMA 12/26/9)."""
    n = len(closes)
    line: list[float | None] = [None] * n
    hist: list[float | None] = [None] * n
    ema_fast = ema_series(closes, fast)
    ema_slow = ema_series(closes, slow)
    for i in range(n):
        a, b = ema_fast[i], ema_slow[i]
        if a is None or b is None:
            continue
        line[i] = a - b
    signal = sma_of_optional(line, signal_period)
    # Seed is SMA; subsequent values use EMA of the MACD line for a closer
    # match to the common 9-period signal EMA. Rebuild signal as EMA from
    # the first full SMA seed.
    signal_ema: list[float | None] = [None] * n
    k = 2.0 / (signal_period + 1)
    seeded = False
    prev: float | None = None
    for i in range(n):
        if line[i] is None:
            continue
        if not seeded:
            if signal[i] is None:
                continue
            signal_ema[i] = signal[i]
            prev = signal[i]
            seeded = True
            continue
        assert prev is not None
        prev = line[i] * k + prev * (1.0 - k)
        signal_ema[i] = prev
    for i in range(n):
        if line[i] is not None and signal_ema[i] is not None:
            hist[i] = line[i] - signal_ema[i]  # type: ignore[operator]
    return {"macd": line, "signal": signal_ema, "hist": hist}


def perfect_order(sma: dict[int, float | None]) -> str | None:
    """Return ``up`` / ``down`` if SMAs 10..200 are stacked, else ``None``."""
    vals = [sma.get(p) for p in SMA_PERIODS]
    if any(v is None for v in vals):
        return None
    seq = [float(v) for v in vals]  # 10, 20, 50, 100, 200
    if all(seq[i] > seq[i + 1] for i in range(len(seq) - 1)):
        return "up"
    if all(seq[i] < seq[i + 1] for i in range(len(seq) - 1)):
        return "down"
    return None


def n_bar_high_low(
    bars: list[dict], n: int = HIGH_N
) -> tuple[float | None, float | None]:
    if n < 1:
        raise IndicatorError("n must be >= 1")
    window = bars[-n:] if len(bars) >= n else bars
    if not window:
        return None, None
    return max(float(b["high"]) for b in window), min(float(b["low"]) for b in window)


def snapshot(bars: list[dict], slope_bars: int = ADX_SLOPE_BARS) -> dict[str, Any]:
    """Last-bar indicator snapshot (small payload for MCP / local models)."""
    _validate_bars(bars)
    if len(bars) < MIN_BARS:
        raise IndicatorError(f"need at least {MIN_BARS} bars; got {len(bars)}")

    closes = _closes(bars)
    highs = _highs(bars)
    lows = _lows(bars)
    last_close = closes[-1]

    sma_last: dict[str, float | None] = {}
    sma_series_map: dict[int, list[float | None]] = {}
    for period in SMA_PERIODS:
        series = sma_series(closes, period)
        sma_series_map[period] = series
        sma_last[str(period)] = _round(_last(series))

    bb = bollinger_series(closes, BB_PERIOD)
    bb_last = {
        "period": BB_PERIOD,
        "mid": _round(_last(bb["mid"])),
        "upper_2": _round(_last(bb["upper_2"])),
        "upper_1": _round(_last(bb["upper_1"])),
        "lower_1": _round(_last(bb["lower_1"])),
        "lower_2": _round(_last(bb["lower_2"])),
    }
    mid = bb_last["mid"]
    u2, l2 = bb_last["upper_2"], bb_last["lower_2"]
    if mid and u2 is not None and l2 is not None and mid != 0:
        bb_last["width"] = _round((u2 - l2) / mid)
    else:
        bb_last["width"] = None
    zone = bb_zone(last_close, bb_last)
    bb_last["zone"] = zone
    double_bb = double_bollinger_state(closes, BB_PERIOD)

    adx = adx_series(bars, ADX_PERIOD)
    adx_now = _last(adx["adx"])
    slope_idx = max(0, len(adx["adx"]) - 1 - slope_bars)
    adx_then = adx["adx"][slope_idx]
    slope = None
    if adx_now is not None and adx_then is not None:
        slope = adx_now - adx_then

    rsi = rsi_series(closes, RSI_PERIOD)
    k_s, d_s = stoch_series(highs, lows, closes)
    macd = macd_series(closes)
    hi_n, lo_n = n_bar_high_low(bars, HIGH_N)

    sma_numeric = {p: sma_last[str(p)] for p in SMA_PERIODS}
    order = perfect_order(sma_numeric)

    close_vs_sma: dict[str, str | None] = {}
    for period in SMA_PERIODS:
        level = sma_last[str(period)]
        if level is None:
            close_vs_sma[str(period)] = None
        elif last_close > level:
            close_vs_sma[str(period)] = "above"
        elif last_close < level:
            close_vs_sma[str(period)] = "below"
        else:
            close_vs_sma[str(period)] = "at"

    missing_sma = [p for p in SMA_PERIODS if sma_last[str(p)] is None]

    from app import lien_geometry

    order_series = lien_geometry.perfect_order_series(sma_series_map)
    order_age = lien_geometry.perfect_order_age(order_series)
    prior = lien_geometry.prior_day_high_low(bars)
    b20 = lien_geometry.breakout_20_state(bars)

    return {
        "bar_count": len(bars),
        "last_time": bars[-1].get("time"),
        "last_close": _round(last_close),
        "sma": sma_last,
        "sma_missing": missing_sma,
        "close_vs_sma": close_vs_sma,
        "ma_perfect_order": order,
        "bollinger": bb_last,
        "double_bb": double_bb,
        "adx": {
            "adx": _round(adx_now, 4),
            "plus_di": _round(_last(adx["plus_di"]), 4),
            "minus_di": _round(_last(adx["minus_di"]), 4),
            "slope": _round(slope, 4),
            "slope_bars": slope_bars,
            "rising": bool(slope is not None and slope > 0),
        },
        "rsi": _round(_last(rsi), 4),
        "stoch": {"k": _round(_last(k_s), 4), "d": _round(_last(d_s), 4)},
        "macd": {
            "macd": _round(_last(macd["macd"])),
            "signal": _round(_last(macd["signal"])),
            "hist": _round(_last(macd["hist"])),
        },
        "high_n": _round(hi_n),
        "low_n": _round(lo_n),
        "high_n_period": HIGH_N,
        "last_high": _round(float(bars[-1]["high"])),
        "last_low": _round(float(bars[-1]["low"])),
        "ma_perfect_order_age": order_age,
        "prior_day": prior,
        "breakout_20": b20,
        "risk_reversals": "unavailable",
        "implied_vol": "unavailable",
    }


def plot_series(
    bars: list[dict],
    lookback: int = 80,
) -> dict[str, Any]:
    """Aligned SMA / Bollinger series for the last ``lookback`` bars (MT4 overlay)."""
    _validate_bars(bars)
    closes = _closes(bars)
    n = len(bars)
    start = max(0, n - lookback)
    sma_map = {p: sma_series(closes, p) for p in SMA_PERIODS}
    bb = bollinger_series(closes, BB_PERIOD)
    return {
        "start_index": start,
        "sma": {str(p): sma_map[p] for p in SMA_PERIODS},
        "bollinger": {
            "upper_2": bb["upper_2"],
            "upper_1": bb["upper_1"],
            "lower_1": bb["lower_1"],
            "lower_2": bb["lower_2"],
        },
    }
