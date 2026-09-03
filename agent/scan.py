"""Scan Lien Ch.7 regimes across a small instrument universe.

Sequential classify (no nested OANDA sweep). Research only; no orders.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from app import indicators, oanda_client, regime as regime_mod

MAX_INSTRUMENTS = 12
DEFAULT_UNIVERSE: tuple[str, ...] = (
    "EUR_USD",
    "GBP_USD",
    "USD_JPY",
    "AUD_USD",
    "USD_CAD",
    "USD_CHF",
    "NZD_USD",
)

ClassifyFn = Callable[[str, str, int, str, str], Awaitable[dict[str, Any]]]


class ScanError(ValueError):
    """Bad universe or filter (not an OANDA/indicator failure)."""


def parse_instruments(instruments: str | Sequence[str] | None) -> list[str]:
    """Split a comma-separated universe; empty → default majors."""
    if instruments is None:
        return list(DEFAULT_UNIVERSE)
    if isinstance(instruments, str):
        parts = [p.strip() for p in instruments.split(",")]
        names = [p for p in parts if p]
    else:
        names = [str(p).strip() for p in instruments if str(p).strip()]
    return names or list(DEFAULT_UNIVERSE)


def compact_row(analysis: dict[str, Any], instrument: str) -> dict[str, Any]:
    """Planner-facing row: labels only, not the full indicator snapshot."""
    return {
        "instrument": instrument,
        "regime": analysis.get("regime"),
        "direction": analysis.get("direction"),
        "trend_waning": bool(analysis.get("trend_waning")),
        "allowed_play_classes": list(analysis.get("allowed_play_classes") or []),
        "confidence": analysis.get("confidence"),
        "last_close": analysis.get("last_close"),
        "error": analysis.get("error"),
    }


async def classify_instrument(
    instrument: str,
    granularity: str = "D",
    count: int = 250,
    from_time: str = "",
    to_time: str = "",
) -> dict[str, Any]:
    """One-pair Ch.7 classify (same candle path as MCP classify_regime)."""
    use_count: int | None = count
    if from_time and to_time:
        use_count = None
    payload = await oanda_client.get_candles(
        instrument,
        granularity=granularity,
        count=use_count,
        price="M",
        from_time=from_time or None,
        to_time=to_time or None,
    )
    bars = oanda_client.candles_to_bars(payload, prefer="mid")
    analysis = regime_mod.analyze_bars(bars)
    analysis["instrument"] = instrument
    analysis["granularity"] = granularity
    if from_time:
        analysis["from_time"] = from_time
    if to_time:
        analysis["to_time"] = to_time
    if use_count is not None:
        analysis["count"] = use_count
    return analysis


def _drop_reason(
    row: dict[str, Any],
    *,
    drop_waning: bool,
    play_class: str,
) -> str | None:
    if row.get("error"):
        return "error"
    if drop_waning and row.get("trend_waning"):
        return "trend_waning"
    if play_class and play_class not in (row.get("allowed_play_classes") or []):
        return "play_class"
    return None


async def scan_regimes(
    instruments: str | Sequence[str] | None = None,
    *,
    granularity: str = "D",
    count: int = 250,
    from_time: str = "",
    to_time: str = "",
    drop_waning: bool = True,
    play_class: str = "",
    classify_fn: ClassifyFn | None = None,
) -> dict[str, Any]:
    """Classify a capped universe; return compact rows plus kept/dropped.

    Per-instrument fetch/classify errors become row ``error`` fields (scan
    continues). ``ScanError`` is only for a universe larger than the cap.
    """
    names = parse_instruments(instruments)
    if len(names) > MAX_INSTRUMENTS:
        raise ScanError(
            f"universe has {len(names)} instruments; max is {MAX_INSTRUMENTS}"
        )
    wanted = play_class.strip()
    classifier = classify_fn or classify_instrument
    rows: list[dict[str, Any]] = []
    for name in names:
        try:
            analysis = await classifier(
                name, granularity, count, from_time, to_time
            )
            row = compact_row(analysis, name)
        except (indicators.IndicatorError, oanda_client.OandaError, Exception) as exc:
            row = compact_row({"error": str(exc)}, name)
            row["error"] = str(exc)
        rows.append(row)

    kept: list[str] = []
    dropped: list[dict[str, str]] = []
    for row in rows:
        reason = _drop_reason(row, drop_waning=drop_waning, play_class=wanted)
        if reason is None:
            kept.append(row["instrument"])
        else:
            dropped.append({"instrument": row["instrument"], "reason": reason})

    return {
        "granularity": granularity,
        "drop_waning": drop_waning,
        "play_class": wanted or None,
        "count": len(rows),
        "rows": rows,
        "kept": kept,
        "dropped": dropped,
    }
