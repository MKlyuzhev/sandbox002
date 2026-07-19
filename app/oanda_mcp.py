"""Read-only OANDA (practice) MCP server.

Exposes FX market-data and account-context tools over stdio for use as a
research MCP set in Cursor. Deliberately read-only: no order placement,
modification, or position-closing tools are defined here.

Run directly (Cursor spawns it this way):

    python app/oanda_mcp.py

Credentials are read from the repo-root ``.env`` (gitignored):

    OANDA_API_KEY=...        # OANDA v20 personal access token
    OANDA_ACCOUNT_ID=...     # e.g. 101-001-1234567-001
    OANDA_ENV=practice       # "practice" (default) or "live"
"""

import logging
import sys
from pathlib import Path

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict
from mcp.server.fastmcp import FastMCP

# stdout is reserved for JSON-RPC; all logging must go to stderr.
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("oanda-research")

_REPO_ROOT = Path(__file__).resolve().parent.parent

_REST_HOSTS = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}


class OandaSettings(BaseSettings):
    # Absolute path so credentials load regardless of the spawning cwd.
    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT / ".env"), extra="ignore"
    )

    oanda_api_key: str = ""
    oanda_account_id: str = ""
    oanda_env: str = "practice"


settings = OandaSettings()


class OandaError(RuntimeError):
    pass


def _base_url() -> str:
    env = settings.oanda_env.strip().lower()
    if env not in _REST_HOSTS:
        raise OandaError(
            f"Invalid OANDA_ENV '{settings.oanda_env}'; expected 'practice' or 'live'."
        )
    return _REST_HOSTS[env]


async def _get(path: str, params: dict | None = None) -> dict:
    """Perform an authenticated read-only GET against the OANDA v20 REST API."""
    if not settings.oanda_api_key:
        raise OandaError("OANDA_API_KEY is not set (add it to .env).")
    url = f"{_base_url()}{path}"
    headers = {
        "Authorization": f"Bearer {settings.oanda_api_key}",
        "Accept-Datetime-Format": "RFC3339",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers, params=params)
    if resp.status_code != 200:
        raise OandaError(f"OANDA GET {path} failed ({resp.status_code}): {resp.text}")
    return resp.json()


def _require_account() -> str:
    if not settings.oanda_account_id:
        raise OandaError("OANDA_ACCOUNT_ID is not set (add it to .env).")
    return settings.oanda_account_id


mcp = FastMCP("oanda-research")


@mcp.tool()
async def list_accounts() -> list[dict]:
    """List the accounts the API token can access (v20 REST only).

    Useful for discovering the correct account id: the v20 REST API serves only
    v20 accounts, so an MT4 account id will not appear here. Each entry includes
    the account ``id`` and its ``tags``. Does not require OANDA_ACCOUNT_ID."""
    data = await _get("/v3/accounts")
    return data.get("accounts", [])


@mcp.tool()
async def get_account_summary() -> dict:
    """Return the OANDA account summary: balance, equity (NAV), unrealized P&L,
    margin used/available, open position/trade counts, and home currency."""
    account_id = _require_account()
    data = await _get(f"/v3/accounts/{account_id}/summary")
    return data.get("account", data)


@mcp.tool()
async def list_instruments() -> list[dict]:
    """List instruments tradeable on the account. Each entry includes name
    (e.g. EUR_USD), type, displayName, pip location, and margin rate."""
    account_id = _require_account()
    data = await _get(f"/v3/accounts/{account_id}/instruments")
    return data.get("instruments", [])


@mcp.tool()
async def get_pricing(instruments: str) -> list[dict]:
    """Get current pricing for one or more instruments.

    Args:
        instruments: Comma-separated instrument names, e.g. "EUR_USD" or
            "EUR_USD,USD_JPY,GBP_USD".

    Returns a list of price objects with bid/ask (closeout) prices, spread
    context, tradeable status, and timestamp.
    """
    account_id = _require_account()
    data = await _get(
        f"/v3/accounts/{account_id}/pricing",
        params={"instruments": instruments},
    )
    return data.get("prices", [])


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
    data = await _get(
        f"/v3/instruments/{instrument}/candles",
        params={"granularity": granularity, "count": count, "price": price},
    )
    return data


@mcp.tool()
async def get_open_positions() -> list[dict]:
    """Return currently open positions on the account (read-only exposure
    snapshot): instrument, long/short units, average price, and unrealized P&L."""
    account_id = _require_account()
    data = await _get(f"/v3/accounts/{account_id}/openPositions")
    return data.get("positions", [])


@mcp.tool()
async def get_open_trades() -> list[dict]:
    """Return currently open trades on the account (read-only): trade id,
    instrument, units, open price, current units, and unrealized P&L."""
    account_id = _require_account()
    data = await _get(f"/v3/accounts/{account_id}/openTrades")
    return data.get("trades", [])


@mcp.tool()
async def get_order_book(instrument: str) -> dict:
    """Get OANDA's order book for an instrument: bucketed resting order volume
    by price, useful as a crowd-positioning research signal."""
    data = await _get(f"/v3/instruments/{instrument}/orderBook")
    return data.get("orderBook", data)


@mcp.tool()
async def get_position_book(instrument: str) -> dict:
    """Get OANDA's position book for an instrument: bucketed open position
    volume (long/short) by price, useful as a crowd-positioning research signal."""
    data = await _get(f"/v3/instruments/{instrument}/positionBook")
    return data.get("positionBook", data)


if __name__ == "__main__":
    logger.info(
        "Starting oanda-research MCP server (env=%s, account_set=%s)",
        settings.oanda_env,
        bool(settings.oanda_account_id),
    )
    mcp.run()
