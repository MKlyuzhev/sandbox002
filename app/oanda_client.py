"""Read-only OANDA v20 REST client (shared by MCP server and CLIs).

Credentials from repo-root ``.env``:

    OANDA_API_KEY=...
    OANDA_ACCOUNT_ID=...
    OANDA_ENV=practice   # or live
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger("oanda")

_REST_HOSTS = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}

MAX_CANDLES_PER_REQUEST = 5000
MAX_CANDLE_PAGES = 100


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


def _parse_rfc3339(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "." in text:
        head, rest = text.split(".", 1)
        tz_sep = "+" if "+" in rest else ("-" if "-" in rest[1:] else "")
        if tz_sep:
            idx = rest.find(tz_sep, 1) if tz_sep == "-" else rest.find(tz_sep)
            frac, tz = rest[:idx], rest[idx:]
        else:
            frac, tz = rest, ""
        frac = (frac + "000000")[:6]
        text = f"{head}.{frac}{tz}"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _format_rfc3339(dt: datetime) -> str:
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond:06d}000Z"


def _shift_rfc3339(value: str, delta: timedelta) -> str | None:
    dt = _parse_rfc3339(value)
    if dt is None:
        return None
    return _format_rfc3339(dt + delta)


def _cmp_time(left: str | None, right: str | None) -> int:
    if not left or not right:
        return 0
    a, b = _parse_rfc3339(left), _parse_rfc3339(right)
    if a is not None and b is not None:
        if a < b:
            return -1
        if a > b:
            return 1
        return 0
    if left < right:
        return -1
    if left > right:
        return 1
    return 0


def _time_key(value: str | None) -> str:
    dt = _parse_rfc3339(value) if value else None
    if dt is None:
        return value or ""
    return dt.isoformat()


async def _candles_request(
    instrument: str,
    granularity: str,
    price: str,
    count: int | None = None,
    from_time: str | None = None,
    to_time: str | None = None,
) -> dict:
    if from_time and to_time and count is not None:
        raise OandaError(
            "OANDA candles: cannot combine from, to, and count; "
            "use count only, from+count, to+count, or from+to."
        )
    params: dict[str, str | int] = {
        "granularity": granularity,
        "price": price,
    }
    if count is not None:
        if count < 1:
            raise OandaError("count must be positive")
        params["count"] = min(int(count), MAX_CANDLES_PER_REQUEST)
    if from_time:
        params["from"] = from_time
    if to_time:
        params["to"] = to_time
    if "count" not in params and not from_time and not to_time:
        params["count"] = 100
    return await get(f"/v3/instruments/{instrument}/candles", params=params)


def _merge_payload(base: dict | None, candles: list[dict]) -> dict:
    out = dict(base or {})
    out["candles"] = candles
    return out


async def _page_forward(
    instrument: str,
    granularity: str,
    price: str,
    from_time: str,
    to_time: str | None = None,
    limit: int | None = None,
) -> dict:
    """Inclusive ``from``; optional inclusive ``to`` and max ``limit`` bars."""
    collected: list[dict] = []
    seen: set[str] = set()
    cursor = from_time
    base: dict | None = None
    for page in range(1, MAX_CANDLE_PAGES + 1):
        remaining = None if limit is None else limit - len(collected)
        if remaining is not None and remaining <= 0:
            break
        batch = MAX_CANDLES_PER_REQUEST if remaining is None else min(
            MAX_CANDLES_PER_REQUEST, remaining
        )
        payload = await _candles_request(
            instrument,
            granularity,
            price,
            count=batch,
            from_time=cursor,
        )
        base = payload
        candles = list(payload.get("candles") or [])
        if not candles:
            break
        past_to = False
        for candle in candles:
            t = candle.get("time")
            if not t:
                continue
            key = _time_key(t)
            if key in seen:
                continue
            if to_time and _cmp_time(t, to_time) > 0:
                past_to = True
                continue
            seen.add(key)
            collected.append(candle)
            if limit is not None and len(collected) >= limit:
                break
        logger.info(
            "OANDA candles page %s: +%s (total %s) %s %s",
            page,
            len(candles),
            len(collected),
            instrument,
            granularity,
        )
        if past_to or (limit is not None and len(collected) >= limit):
            break
        if len(candles) < batch:
            break
        nxt = _shift_rfc3339(candles[-1].get("time") or "", timedelta(microseconds=1))
        if not nxt or nxt == cursor:
            break
        cursor = nxt
    else:
        raise OandaError(
            f"OANDA candles: exceeded {MAX_CANDLE_PAGES} pages "
            f"({MAX_CANDLES_PER_REQUEST} each); shrink the window."
        )
    return _merge_payload(base, collected)


async def _page_backward(
    instrument: str,
    granularity: str,
    price: str,
    limit: int,
    to_time: str | None = None,
) -> dict:
    """Most-recent ``limit`` bars, optionally ending at ``to_time``."""
    chunks: list[list[dict]] = []
    seen: set[str] = set()
    to_cursor = to_time
    base: dict | None = None
    total = 0
    for page in range(1, MAX_CANDLE_PAGES + 1):
        remaining = limit - total
        if remaining <= 0:
            break
        batch = min(MAX_CANDLES_PER_REQUEST, remaining)
        payload = await _candles_request(
            instrument,
            granularity,
            price,
            count=batch,
            to_time=to_cursor,
        )
        base = payload
        candles = list(payload.get("candles") or [])
        if not candles:
            break
        unique = []
        for candle in candles:
            t = candle.get("time")
            if not t:
                continue
            key = _time_key(t)
            if key in seen:
                continue
            seen.add(key)
            unique.append(candle)
        chunks.append(unique)
        total += len(unique)
        logger.info(
            "OANDA candles page %s: +%s (total %s) %s %s",
            page,
            len(candles),
            total,
            instrument,
            granularity,
        )
        if len(candles) < batch:
            break
        first = candles[0].get("time")
        nxt = _shift_rfc3339(first or "", timedelta(microseconds=-1))
        if not nxt or nxt == to_cursor:
            break
        to_cursor = nxt
    else:
        raise OandaError(
            f"OANDA candles: exceeded {MAX_CANDLE_PAGES} pages "
            f"({MAX_CANDLES_PER_REQUEST} each); shrink count."
        )
    collected: list[dict] = []
    for chunk in reversed(chunks):
        collected.extend(chunk)
    if len(collected) > limit:
        collected = collected[-limit:]
    return _merge_payload(base, collected)


async def get_candles(
    instrument: str,
    granularity: str = "H1",
    count: int | None = 100,
    price: str = "MBA",
    from_time: str | None = None,
    to_time: str | None = None,
) -> dict:
    """Fetch candles, paging at OANDA's 5000-bar cap.

    Allowed shapes: count only; from+count; to+count; from+to.
    ``from``+``to`` windows larger than 5000 bars are fetched in 5000-bar
    pages. ``count`` larger than 5000 is also paged. Datetimes should be
    RFC3339 (e.g. ``2024-06-01T00:00:00.000000000Z``).
    """
    if from_time and to_time and count is not None:
        raise OandaError(
            "OANDA candles: cannot combine from, to, and count; "
            "use count only, from+count, to+count, or from+to."
        )
    if not from_time and not to_time and count is None:
        count = 100

    if from_time and to_time:
        try:
            return await _candles_request(
                instrument,
                granularity,
                price,
                from_time=from_time,
                to_time=to_time,
            )
        except OandaError as exc:
            if "(400)" not in str(exc):
                raise
            logger.info(
                "OANDA from+to exceeded 5000 bars; paging %s %s",
                instrument,
                granularity,
            )
            return await _page_forward(
                instrument,
                granularity,
                price,
                from_time,
                to_time=to_time,
            )

    if count is not None and count > MAX_CANDLES_PER_REQUEST:
        if from_time:
            return await _page_forward(
                instrument,
                granularity,
                price,
                from_time,
                limit=count,
            )
        return await _page_backward(
            instrument,
            granularity,
            price,
            limit=count,
            to_time=to_time,
        )

    return await _candles_request(
        instrument,
        granularity,
        price,
        count=count,
        from_time=from_time,
        to_time=to_time,
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
                "complete": bool(c.get("complete", True)),
            }
        )
    return bars


def _side_ohlc(component: dict) -> dict[str, float]:
    return {
        "o": float(component["o"]),
        "h": float(component["h"]),
        "l": float(component["l"]),
        "c": float(component["c"]),
    }


def bars_with_ba(payload: dict) -> list[dict]:
    """Mid OHLC bars plus ``bid`` / ``ask`` ``{o,h,l,c}`` from an MBA payload.

    Candles missing mid are skipped (same as ``candles_to_bars``). Complete
    candles missing bid or ask are still returned as mid-only; rest-mode walks
    raise ``WalkError`` when they try to fill or exit those bars. Incomplete
    candles without bid/ask are skipped.
    """
    bars: list[dict] = []
    for c in payload.get("candles") or []:
        mid = c.get("mid")
        if not mid:
            continue
        complete = bool(c.get("complete", True))
        bid = c.get("bid")
        ask = c.get("ask")
        if (not bid or not ask) and not complete:
            continue
        bar = {
            "time": c.get("time"),
            "open": float(mid["o"]),
            "high": float(mid["h"]),
            "low": float(mid["l"]),
            "close": float(mid["c"]),
            "volume": c.get("volume"),
            "complete": complete,
        }
        if bid and ask:
            bar["bid"] = _side_ohlc(bid)
            bar["ask"] = _side_ohlc(ask)
        bars.append(bar)
    return bars
