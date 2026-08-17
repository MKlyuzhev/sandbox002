"""Read-only OANDA (practice) MCP server.

Exposes FX market-data and account-context tools over stdio for use as a
research MCP set in Cursor. Deliberately read-only: no order placement,
modification, or position-closing tools are defined here. MT4 helpers only
draw chart objects via a file inbox (see ``app.mt4_bridge``).

Run directly (Cursor spawns it this way):

    python app/oanda_mcp.py

Credentials are read from the repo-root ``.env`` (gitignored) via
``app.oanda_client``.
"""

import logging
import os
import sys
from pathlib import Path

# Cursor may spawn this from any cwd; pin repo root for package imports.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
os.chdir(_REPO_ROOT)

from mcp.server.fastmcp import FastMCP  # noqa: E402

from app import mt4_bridge, oanda_client  # noqa: E402

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("oanda-research")

mcp = FastMCP("oanda-research")


@mcp.tool()
async def list_accounts() -> list[dict]:
    """List the accounts the API token can access (v20 REST only).

    Useful for discovering the correct account id: the v20 REST API serves only
    v20 accounts, so an MT4 account id will not appear here. Each entry includes
    the account ``id`` and its ``tags``. Does not require OANDA_ACCOUNT_ID."""
    return await oanda_client.list_accounts()


@mcp.tool()
async def get_account_summary() -> dict:
    """Return the OANDA account summary: balance, equity (NAV), unrealized P&L,
    margin used/available, open position/trade counts, and home currency."""
    return await oanda_client.get_account_summary()


@mcp.tool()
async def list_instruments() -> list[dict]:
    """List instruments tradeable on the account. Each entry includes name
    (e.g. EUR_USD), type, displayName, pip location, and margin rate."""
    return await oanda_client.list_instruments()


@mcp.tool()
async def get_pricing(instruments: str) -> list[dict]:
    """Get current pricing for one or more instruments.

    Args:
        instruments: Comma-separated instrument names, e.g. "EUR_USD" or
            "EUR_USD,USD_JPY,GBP_USD".

    Returns a list of price objects with bid/ask (closeout) prices, spread
    context, tradeable status, and timestamp.
    """
    return await oanda_client.get_pricing(instruments)


@mcp.tool()
async def get_candles(
    instrument: str,
    granularity: str = "H1",
    count: int = 100,
    price: str = "MBA",
    from_time: str = "",
    to_time: str = "",
) -> dict:
    """Get historical OHLC candles for an instrument.

    Args:
        instrument: Instrument name, e.g. "EUR_USD".
        granularity: Candle size, e.g. "S5", "M1", "M5", "M15", "H1", "H4",
            "D", "W". Defaults to "H1".
        count: Number of candles. Defaults to 100. Windows larger than
            5000 bars are fetched in pages automatically. Omit-style
            historical windows: use from_time+to_time without relying on all
            three together (OANDA forbids from+to+count).
        price: Which price components: "M" (mid), "B" (bid), "A" (ask), or
            "MBA". Defaults to "MBA".
        from_time: Optional RFC3339 start (e.g. 2024-01-01T00:00:00.000000000Z).
        to_time: Optional RFC3339 end. With count alone ending at ``to_time``,
            use to_time+count for N candles ending at that time.

    Returns the OANDA candles payload with an ``candles`` list of OHLC values.
    """
    return await oanda_client.get_candles(
        instrument,
        granularity=granularity,
        count=count,
        price=price,
        from_time=from_time or None,
        to_time=to_time or None,
    )


@mcp.tool()
async def get_open_positions() -> list[dict]:
    """Return currently open positions on the account (read-only exposure
    snapshot): instrument, long/short units, average price, and unrealized P&L."""
    return await oanda_client.get_open_positions()


@mcp.tool()
async def get_open_trades() -> list[dict]:
    """Return currently open trades on the account (read-only): trade id,
    instrument, units, open price, current units, and unrealized P&L."""
    return await oanda_client.get_open_trades()


@mcp.tool()
async def get_order_book(instrument: str) -> dict:
    """Get OANDA's order book for an instrument: bucketed resting order volume
    by price, useful as a crowd-positioning research signal."""
    return await oanda_client.get_order_book(instrument)


@mcp.tool()
async def get_position_book(instrument: str) -> dict:
    """Get OANDA's position book for an instrument: bucketed open position
    volume (long/short) by price, useful as a crowd-positioning research signal."""
    return await oanda_client.get_position_book(instrument)


@mcp.tool()
async def mt4_status() -> dict:
    """MT4 bridge status: inbox path/writable, EA heartbeat age, chart
    symbol/timeframe, last command id, and last EA error.

    Call this before drawing. Requires SandboxChartBridge.mq4 attached to a
    chart (AutoTrading can stay off — objects only, no orders).
    """
    return mt4_bridge.status()


@mcp.tool()
async def mt4_upsert_objects(
    symbol: str,
    timeframe: str,
    objects: list[dict],
    prefix: str = "sbox.",
    clear_prefix_first: bool = True,
) -> dict:
    """Upsert chart objects on the EA's chart (display only; no orders).

    Args:
        symbol: OANDA or MT4 symbol (GBP_USD or GBPUSD). Must match the chart.
        timeframe: Granularity such as H1, M15, D. Must match the chart.
        objects: List of dicts with name, type (trend|hline|vline|text|arrow|
            rectangle|label), t1/p1, optional t2/p2, color, style, width,
            text, ray, arrow_code, x, y.
        prefix: Prepended to names that do not already start with it.
        clear_prefix_first: Delete existing objects with this prefix first.

    Refuses if the EA heartbeat is stale or the chart symbol/TF does not match.
    """
    return mt4_bridge.upsert_objects(
        symbol,
        timeframe,
        objects,
        prefix=prefix,
        clear_prefix_first=clear_prefix_first,
    )


@mcp.tool()
async def mt4_delete_objects(
    names: list[str] | None = None,
    prefix: str = "",
) -> dict:
    """Delete chart objects by exact names and/or name prefix (e.g. sbox.formation.)."""
    return mt4_bridge.delete_objects(names=names or [], prefix=prefix)


@mcp.tool()
async def mt4_clear_layer(prefix: str = "sbox.") -> dict:
    """Remove all sandbox chart objects whose names start with prefix."""
    return mt4_bridge.clear_layer(prefix=prefix)


@mcp.tool()
async def mt4_draw_formation(
    instrument: str,
    granularity: str = "H1",
    count: int = 200,
    from_time: str = "",
    to_time: str = "",
    swing_left: int = 3,
    swing_right: int = 3,
    max_lines: int = 5,
    break_frac: float = 0.001,
    prefix: str = "sbox.formation.",
) -> dict:
    """Analyze OANDA candles and draw formation overlays on the MT4 chart.

    Same geometry as scripts/analyze_formation.py / formation_plot.py: swings,
    support/resistance trendlines, H&S LS/H/RS labels, neckline, min_target.
    Refuses if SandboxChartBridge is not on a matching symbol/timeframe chart.

    Returns analysis JSON plus cmd_id, objects_written, and chart_ok.
    """
    return await mt4_bridge.draw_formation(
        instrument,
        granularity=granularity,
        count=count,
        from_time=from_time or None,
        to_time=to_time or None,
        swing_left=swing_left,
        swing_right=swing_right,
        max_lines=max_lines,
        break_frac=break_frac,
        prefix=prefix,
    )


async def _analyze_regime(
    instrument: str,
    granularity: str,
    count: int,
    from_time: str,
    to_time: str,
) -> tuple[list[dict], dict]:
    from app import regime

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
    analysis = regime.analyze_bars(bars)
    analysis["instrument"] = instrument
    analysis["granularity"] = granularity
    if from_time:
        analysis["from_time"] = from_time
    if to_time:
        analysis["to_time"] = to_time
    if use_count is not None:
        analysis["count"] = use_count
    return bars, analysis


@mcp.tool()
async def classify_regime(
    instrument: str,
    granularity: str = "D",
    count: int = 250,
    from_time: str = "",
    to_time: str = "",
) -> dict:
    """Lien Ch.7 trend/range checklist from OANDA candles (deterministic).

    Computes ADX, double Bollinger, SMA stack, RSI/stoch/MACD in code — do not
    recompute those in the model. Default granularity D (Lien's journal).
    count=250 covers the 200-SMA. Risk reversals and implied vol are marked
    unavailable. Research only; no orders.

    Returns regime (trend|range|mixed), direction, checklist X-counts,
    allowed_play_classes, last-bar snapshot, and notes. On too few bars,
    returns an ``error`` field.
    """
    from app import indicators

    try:
        _bars, analysis = await _analyze_regime(
            instrument, granularity, count, from_time, to_time
        )
    except indicators.IndicatorError as exc:
        return {"error": str(exc), "instrument": instrument, "granularity": granularity}
    return analysis


@mcp.tool()
async def indicator_snapshot(
    instrument: str,
    granularity: str = "D",
    count: int = 250,
    from_time: str = "",
    to_time: str = "",
) -> dict:
    """Last-bar SMA / double Bollinger / ADX / RSI / stoch / MACD snapshot.

    Same numbers as classify_regime without Lien regime labels. Useful for
    debugging or later strategy chapters. Raises if fewer than 30 bars.
    """
    from app import indicators

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
    try:
        snap = indicators.snapshot(bars)
    except indicators.IndicatorError as exc:
        return {
            "error": str(exc),
            "instrument": instrument,
            "granularity": granularity,
            "bar_count": len(bars),
        }
    snap["instrument"] = instrument
    snap["granularity"] = granularity
    return snap


@mcp.tool()
async def mt4_draw_regime(
    instrument: str,
    granularity: str = "D",
    count: int = 250,
    from_time: str = "",
    to_time: str = "",
    prefix: str = "sbox.regime.",
) -> dict:
    """Classify Lien regime and draw bands / SMA stack on the MT4 chart.

    Price pane only: double Bollinger, SMA 10/20/50 (100/200 dashed), 10-bar
    high/low, corner regime label, and a color legend. Does not draw
    ADX/RSI/MACD panes. Prefix sbox.regime. does not clear sbox.formation.
    Refuses if the EA chart symbol/timeframe does not match (Daily
    classification needs D1).
    """
    return await mt4_bridge.draw_regime(
        instrument,
        granularity=granularity,
        count=count,
        from_time=from_time or None,
        to_time=to_time or None,
        prefix=prefix,
    )


@mcp.tool()
async def mt4_draw_ticket(
    instrument: str = "",
    granularity: str = "D",
    side: str = "none",
    entry: float = 0.0,
    stop: float = 0.0,
    target: float = 0.0,
    prefix: str = "sbox.ticket.",
    run_id: str = "",
    at_time: str = "",
) -> dict:
    """Draw a paper/signal ticket on MT4 (entry/stop/target hlines). No orders.

    Pass explicit prices, or ``run_id`` to load the proposal from the agent
    journal. ``at_time`` is RFC3339 for the decision bar (vline + arrow);
    ``run_id`` defaults to the run's ``regime.last_time``. Prefix
    ``sbox.ticket.`` does not clear ``sbox.regime.``. Refuses if the EA chart
    symbol/timeframe does not match. Display only.
    """
    inst = instrument
    gran = granularity
    ticket_side = side
    ticket_entry, ticket_stop, ticket_target = entry, stop, target
    ticket_at = at_time.strip() or None
    if run_id.strip():
        from agent.journal import Journal

        record = Journal().get_run(run_id.strip())
        if record is None:
            return {"ok": False, "error": f"run not found: {run_id}"}
        proposal = record.proposal
        if proposal is None or proposal.entry is None or proposal.stop is None or proposal.target is None:
            return {
                "ok": False,
                "error": "run has no complete ticket (entry/stop/target)",
                "run_id": record.run_id,
                "action": record.action,
            }
        inst = instrument.strip() or record.instrument
        gran = granularity if instrument.strip() else record.granularity
        ticket_side = side if side not in ("", "none") else proposal.side
        ticket_entry = proposal.entry
        ticket_stop = proposal.stop
        ticket_target = proposal.target
        if not ticket_at:
            ticket_at = (record.regime or {}).get("last_time") or record.ts
    if not inst:
        return {"ok": False, "error": "instrument is required (or pass run_id)"}
    return mt4_bridge.apply_ticket(
        inst,
        gran,
        ticket_entry,
        ticket_stop,
        ticket_target,
        side=ticket_side,
        prefix=prefix,
        at_time=ticket_at,
    )


if __name__ == "__main__":
    logger.info(
        "Starting oanda-research MCP server (env=%s, account_set=%s)",
        oanda_client.settings.oanda_env,
        bool(oanda_client.settings.oanda_account_id),
    )
    mcp.run()
