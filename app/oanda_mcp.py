"""Read-only OANDA (practice) MCP server.

Exposes FX market-data and account-context tools over stdio for use as a
research MCP set in Cursor. Deliberately read-only: no order placement,
modification, or position-closing tools are defined here.

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

from app import oanda_client  # noqa: E402

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
) -> dict:
    """Get historical OHLC candles for an instrument.

    Args:
        instrument: Instrument name, e.g. "EUR_USD".
        granularity: Candle size, e.g. "S5", "M1", "M5", "M15", "H1", "H4",
            "D", "W". Defaults to "H1".
        count: Number of candles to return (max 5000). Defaults to 100.
        price: Which price components to return: "M" (mid), "B" (bid),
            "A" (ask), or a combination like "MBA". Defaults to "MBA".

    Returns the OANDA candles payload with an ``candles`` list of OHLC values.
    """
    return await oanda_client.get_candles(
        instrument, granularity=granularity, count=count, price=price
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


if __name__ == "__main__":
    logger.info(
        "Starting oanda-research MCP server (env=%s, account_set=%s)",
        oanda_client.settings.oanda_env,
        bool(oanda_client.settings.oanda_account_id),
    )
    mcp.run()
