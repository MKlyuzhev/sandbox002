"""Read-only OANDA v20 REST client (shared by MCP server and CLIs).

Credentials from repo-root ``.env``:

    OANDA_API_KEY=...
    OANDA_ACCOUNT_ID=...
    OANDA_ENV=practice   # or live
"""

from __future__ import annotations

from pathlib import Path

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parent.parent

_REST_HOSTS = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}


class OandaSettings(BaseSettings):
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


async def get(path: str, params: dict | None = None) -> dict:
    """Authenticated read-only GET against the OANDA v20 REST API."""
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


def require_account() -> str:
    if not settings.oanda_account_id:
        raise OandaError("OANDA_ACCOUNT_ID is not set (add it to .env).")
    return settings.oanda_account_id


async def list_accounts() -> list[dict]:
    data = await get("/v3/accounts")
    return data.get("accounts", [])


async def get_account_summary() -> dict:
    account_id = require_account()
    data = await get(f"/v3/accounts/{account_id}/summary")
    return data.get("account", data)


async def list_instruments() -> list[dict]:
    account_id = require_account()
    data = await get(f"/v3/accounts/{account_id}/instruments")
    return data.get("instruments", [])


async def get_pricing(instruments: str) -> list[dict]:
    account_id = require_account()
    data = await get(
        f"/v3/accounts/{account_id}/pricing",
        params={"instruments": instruments},
    )
    return data.get("prices", [])


async def get_candles(
    instrument: str,
    granularity: str = "H1",
    count: int = 100,
    price: str = "MBA",
) -> dict:
    return await get(
        f"/v3/instruments/{instrument}/candles",
        params={"granularity": granularity, "count": count, "price": price},
    )


async def get_open_positions() -> list[dict]:
    account_id = require_account()
    data = await get(f"/v3/accounts/{account_id}/openPositions")
    return data.get("positions", [])


async def get_open_trades() -> list[dict]:
    account_id = require_account()
    data = await get(f"/v3/accounts/{account_id}/openTrades")
    return data.get("trades", [])


async def get_order_book(instrument: str) -> dict:
    data = await get(f"/v3/instruments/{instrument}/orderBook")
    return data.get("orderBook", data)


async def get_position_book(instrument: str) -> dict:
    data = await get(f"/v3/instruments/{instrument}/positionBook")
    return data.get("positionBook", data)


def candles_to_bars(payload: dict, prefer: str = "mid") -> list[dict]:
    """Normalize an OANDA candles response to OHLC bar dicts.

    Args:
        payload: Raw ``get_candles`` response.
        prefer: Price component key: ``mid``, ``bid``, or ``ask``.
    """
    bars: list[dict] = []
    for c in payload.get("candles") or []:
        if not c.get("complete", True):
            # Include incomplete last candle for live research; still usable.
            pass
        component = c.get(prefer) or c.get("mid") or c.get("bid") or c.get("ask")
        if not component:
            continue
        bars.append(
            {
                "time": c.get("time"),
                "open": float(component["o"]),
                "high": float(component["h"]),
                "low": float(component["l"]),
                "close": float(component["c"]),
                "volume": c.get("volume"),
            }
        )
    return bars
