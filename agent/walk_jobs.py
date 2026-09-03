"""Causal walk dispatch shared by CLI and MCP. Research only; no broker orders."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from agent.event_walk import walk_event
from agent.fader_walk import walk_fader
from agent.journal import DEFAULT_DB_PATH, Journal
from agent.lien_chapters import CHAPTER_TO_ENGINE, EVENT_ENGINES, entry_lien_error
from agent.mtf_walk import walk_mtf
from agent.paper_walk import walk_paper
from agent.schema import FillMode, Goal, WalkResult
from app import indicators, oanda_client, regime_walk
from app.walk_fetch import fetch_walk_bars

WalkKind = Literal["ch7", "mtf", "lien"]
TRADE_HEAD = 10
TRADE_TAIL = 10
MAX_INLINE_TRADES = TRADE_HEAD + TRADE_TAIL

FetchWalkFn = Callable[..., Awaitable[list[dict[str, Any]]]]


class WalkJobError(ValueError):
    """Bad kind/chapter (not a fetch or walk-runtime failure)."""


def truncate_trades(trades: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """Keep all trades if small; else first 10 + last 10."""
    if len(trades) <= MAX_INLINE_TRADES:
        return trades, False
    return trades[:TRADE_HEAD] + trades[-TRADE_TAIL:], True


def _paper_goal(
    instrument: str,
    granularity: str,
    from_time: str,
    to_time: str,
    *,
    ltf_granularity: str = "H1",
    risk_fraction: float,
    balance: float,
    exposure_cap: float,
    value_per_price_unit: float,
    fill_mode: FillMode,
) -> Goal:
    return Goal(
        instrument=instrument,
        granularity=granularity,
        ltf_granularity=ltf_granularity,
        mode="paper",
        from_time=from_time,
        to_time=to_time,
        risk_fraction=risk_fraction,
        balance=balance,
        exposure_cap=exposure_cap,
        value_per_price_unit=value_per_price_unit,
        fill_mode=fill_mode,
        no_rag=True,
        no_llm=True,
    )


async def execute_walk(
    kind: str,
    instrument: str,
    from_time: str,
    to_time: str,
    *,
    chapter: int | None = None,
    granularity: str = "D",
    ltf_granularity: str = "H1",
    lookback: int = regime_walk.DEFAULT_LOOKBACK,
    fill_mode: FillMode = "close",
    balance: float = 10_000.0,
    risk_fraction: float = 0.02,
    exposure_cap: float = 0.06,
    value_per_price_unit: float = 1.0,
    journal: Journal | None = None,
    no_journal: bool = False,
    fetch_fn: FetchWalkFn | None = None,
    entry_mode: str = "first_fire",
) -> tuple[WalkResult, dict[str, Any]]:
    """Fetch bars and run the matching causal walk. Returns (result, meta).

    ``meta`` may include bar lists for CLI overlays; MCP must not dump them.
    """
    kind_key = kind.strip().lower()
    if kind_key not in ("ch7", "mtf", "lien"):
        raise WalkJobError(f"unknown walk kind {kind!r}; use ch7, mtf, or lien")
    if not from_time or not to_time:
        raise WalkJobError("from_time and to_time are required")
    fetch = fetch_fn or fetch_walk_bars
    with_ba = fill_mode == "rest"
    store = None if no_journal else (journal if journal is not None else Journal(DEFAULT_DB_PATH))
    goal = _paper_goal(
        instrument,
        granularity,
        from_time,
        to_time,
        ltf_granularity=ltf_granularity,
        risk_fraction=risk_fraction,
        balance=balance,
        exposure_cap=exposure_cap,
        value_per_price_unit=value_per_price_unit,
        fill_mode=fill_mode,
    )
    meta: dict[str, Any] = {
        "kind": kind_key,
        "instrument": instrument,
        "granularity": granularity,
        "from_time": from_time,
        "to_time": to_time,
        "lookback": lookback,
        "fill_mode": fill_mode,
        "value_per_price_unit": value_per_price_unit,
    }

    if kind_key == "ch7":
        bars = await fetch(
            instrument, granularity, from_time, to_time, lookback, with_ba=with_ba
        )
        start_index = regime_walk.first_index_on_or_after(bars, from_time)
        result = walk_paper(
            bars, goal, lookback=lookback, start_index=start_index, journal=store
        )
        meta.update(
            {
                "bars": bars,
                "bar_count": len(bars),
                "start_index": start_index,
                "engine": "ch7_geometry",
                "chapter": 7,
            }
        )
        return result, meta

    if kind_key == "mtf":
        htf_bars, ltf_bars = await asyncio.gather(
            fetch(
                instrument, granularity, from_time, to_time, lookback, with_ba=False
            ),
            fetch(
                instrument,
                ltf_granularity,
                from_time,
                to_time,
                lookback,
                with_ba=with_ba,
            ),
        )
        start_index = regime_walk.first_index_on_or_after(ltf_bars, from_time)
        result = walk_mtf(
            htf_bars,
            ltf_bars,
            goal,
            lookback=lookback,
            start_index=start_index,
            entry_mode=entry_mode,  # type: ignore[arg-type]
            journal=store,
        )
        meta.update(
            {
                "htf_bars": htf_bars,
                "ltf_bars": ltf_bars,
                "htf_bar_count": len(htf_bars),
                "ltf_bar_count": len(ltf_bars),
                "ltf_granularity": ltf_granularity,
                "start_index": start_index,
                "entry_mode": entry_mode,
                "engine": "mtf",
                "chapter": 8,
            }
        )
        return result, meta

    if chapter is None:
        raise WalkJobError("lien walks require chapter (9, 13, 14, or 16)")
    engine = CHAPTER_TO_ENGINE.get(chapter)
    if engine is None:
        raise WalkJobError(entry_lien_error(chapter))
    meta["chapter"] = chapter
    meta["engine"] = engine
    if engine == "fader":
        htf_bars, ltf_bars = await asyncio.gather(
            fetch(
                instrument, granularity, from_time, to_time, lookback, with_ba=False
            ),
            fetch(
                instrument,
                ltf_granularity,
                from_time,
                to_time,
                lookback,
                with_ba=with_ba,
            ),
        )
        start_index = regime_walk.first_index_on_or_after(ltf_bars, from_time)
        result = walk_fader(
            htf_bars,
            ltf_bars,
            goal,
            lookback=lookback,
            start_index=start_index,
            journal=store,
        )
        meta.update(
            {
                "htf_bars": htf_bars,
                "ltf_bars": ltf_bars,
                "htf_bar_count": len(htf_bars),
                "ltf_bar_count": len(ltf_bars),
                "ltf_granularity": ltf_granularity,
                "start_index": start_index,
            }
        )
        return result, meta
    if engine not in EVENT_ENGINES:
        raise WalkJobError(f"walk_lien: engine {engine} is not an event walk")
    bars = await fetch(
        instrument, granularity, from_time, to_time, lookback, with_ba=with_ba
    )
    start_index = regime_walk.first_index_on_or_after(bars, from_time)
    result = walk_event(
        bars,
        goal,
        engine,
        lookback=lookback,
        start_index=start_index,
        journal=store,
    )
    meta.update({"bars": bars, "bar_count": len(bars), "start_index": start_index})
    return result, meta


def compact_walk_payload(
    result: WalkResult,
    meta: dict[str, Any],
    *,
    truncate: bool = True,
) -> dict[str, Any]:
    """JSON for MCP/CLI. Drops raw bar lists."""
    trades = [t.model_dump(mode="json") for t in result.trades]
    truncated = False
    if truncate:
        trades, truncated = truncate_trades(trades)
    skip = {"bars", "htf_bars", "ltf_bars"}
    out = {k: v for k, v in meta.items() if k not in skip}
    out.update(
        {
            "walk_id": result.walk_id,
            "equity": result.equity.model_dump(mode="json"),
            "trades": trades,
            "trade_count": len(result.trades),
            "trades_truncated": truncated,
        }
    )
    return out


async def run_walk(
    kind: str,
    instrument: str,
    from_time: str,
    to_time: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """MCP-facing: execute + compact payload (truncated trades)."""
    result, meta = await execute_walk(
        kind, instrument, from_time, to_time, **kwargs
    )
    return compact_walk_payload(result, meta, truncate=True)


def walk_job_error_payload(exc: BaseException) -> dict[str, Any]:
    return {"error": str(exc)}


# Re-export for MCP exception mapping
WalkRuntime = (oanda_client.OandaError, regime_walk.WalkError, indicators.IndicatorError)
