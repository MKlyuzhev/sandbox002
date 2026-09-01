"""Shared warmup + [--from, --to] candle fetch for causal walks."""

from __future__ import annotations

from app import oanda_client, regime_walk


async def fetch_walk_bars(
    instrument: str,
    granularity: str,
    from_time: str,
    to_time: str,
    lookback: int,
    *,
    with_ba: bool = False,
) -> list[dict]:
    """Warmup bars ending at ``from_time``, then the test range. Complete only.

    ``with_ba=False`` (default, ``--fill close``): mid-only. ``with_ba=True``
    (``--fill rest``): MBA candles with mid OHLC plus ``bid`` / ``ask``.
    """
    if lookback > regime_walk.MAX_BARS:
        raise regime_walk.WalkError(
            f"lookback {lookback} exceeds OANDA max {regime_walk.MAX_BARS}"
        )
    price = "MBA" if with_ba else "M"
    warmup_payload = await oanda_client.get_candles(
        instrument,
        granularity=granularity,
        count=lookback,
        price=price,
        to_time=from_time,
    )
    test_payload = await oanda_client.get_candles(
        instrument,
        granularity=granularity,
        count=None,
        price=price,
        from_time=from_time,
        to_time=to_time,
    )
    if with_ba:
        warmup = oanda_client.bars_with_ba(warmup_payload)
        test = oanda_client.bars_with_ba(test_payload)
    else:
        warmup = oanda_client.candles_to_bars(warmup_payload, prefer="mid")
        test = oanda_client.candles_to_bars(test_payload, prefer="mid")
    if len(test) >= regime_walk.MAX_BARS:
        raise regime_walk.WalkError(
            f"test range returned {len(test)} bars; OANDA max is "
            f"{regime_walk.MAX_BARS}. Shrink --from/--to."
        )
    bars = regime_walk.prepare_bars(list(warmup) + list(test))
    bars = regime_walk.drop_after(bars, to_time)
    if with_ba:
        for bar in bars:
            if "bid" not in bar or "ask" not in bar:
                raise regime_walk.WalkError(
                    f"rest fetch missing bid/ask at {bar.get('time')}; "
                    "no mid fallback"
                )
    return bars
