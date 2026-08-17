"""Shared warmup + [--from, --to] candle fetch for causal walks."""

from __future__ import annotations

from app import oanda_client, regime_walk


async def fetch_walk_bars(
    instrument: str,
    granularity: str,
    from_time: str,
    to_time: str,
    lookback: int,
) -> list[dict]:
    """Warmup bars ending at ``from_time``, then the test range. Complete only."""
    if lookback > regime_walk.MAX_BARS:
        raise regime_walk.WalkError(
            f"lookback {lookback} exceeds OANDA max {regime_walk.MAX_BARS}"
        )
    warmup_payload = await oanda_client.get_candles(
        instrument,
        granularity=granularity,
        count=lookback,
        price="M",
        to_time=from_time,
    )
    test_payload = await oanda_client.get_candles(
        instrument,
        granularity=granularity,
        count=None,
        price="M",
        from_time=from_time,
        to_time=to_time,
    )
    warmup = oanda_client.candles_to_bars(warmup_payload, prefer="mid")
    test = oanda_client.candles_to_bars(test_payload, prefer="mid")
    if len(test) >= regime_walk.MAX_BARS:
        raise regime_walk.WalkError(
            f"test range returned {len(test)} bars; OANDA max is "
            f"{regime_walk.MAX_BARS}. Shrink --from/--to."
        )
    bars = regime_walk.prepare_bars(list(warmup) + list(test))
    return regime_walk.drop_after(bars, to_time)
