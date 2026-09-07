"""Currency pairs named in corpus source ``lien-fx``.

Compiled from ingested Chroma chunks (concatenated and EUR/USD-style tickers).
Inverted quotes (``CADUSD``, ``USDEUR``, …) fold to OANDA ``BASE_QUOTE``.
Historical DEM names stay in ``HISTORICAL`` and are not a live research pool.
``scan_regimes`` default is ``USD_MAJORS``; pass ``lien-fx`` or a CSV of OANDA
names for the rest of the pool. Research only; no orders.
"""

from __future__ import annotations

from collections.abc import Sequence

# Seven USD majors. Empty ``scan_regimes`` instruments still resolve here.
USD_MAJORS: tuple[str, ...] = (
    "EUR_USD",
    "GBP_USD",
    "USD_JPY",
    "AUD_USD",
    "USD_CAD",
    "USD_CHF",
    "NZD_USD",
)

# Ch. 4 session-volatility board (Figures 4.2–4.4).
CH4_SESSION_BOARD: tuple[str, ...] = (
    "EUR_GBP",
    "AUD_USD",
    "NZD_USD",
    "USD_CAD",
    "EUR_USD",
    "EUR_CHF",
    "AUD_JPY",
    "GBP_USD",
    "USD_CHF",
    "USD_JPY",
    "GBP_CHF",
    "GBP_JPY",
)

# Live OANDA names mentioned in the book (folded inversions; no DEM).
RESEARCH_POOL: tuple[str, ...] = USD_MAJORS + (
    "EUR_GBP",
    "EUR_JPY",
    "EUR_CHF",
    "EUR_CAD",
    "GBP_JPY",
    "GBP_CHF",
    "AUD_JPY",
    "AUD_CAD",
    "AUD_CHF",
    "AUD_NZD",
    "NZD_CAD",
    "NZD_JPY",
    "CHF_JPY",
    "USD_SGD",
)

# Plaza / ERM-era examples. Not OANDA practice instruments.
HISTORICAL: tuple[str, ...] = (
    "GBP_DEM",
    "DEM_JPY",
)

ALIASES: dict[str, tuple[str, ...]] = {
    "usd-majors": USD_MAJORS,
    "lien-fx-ch4": CH4_SESSION_BOARD,
    "lien-fx": RESEARCH_POOL,
}


def resolve_alias(token: str) -> tuple[str, ...] | None:
    """Map a single pool name (``usd-majors``, ``lien-fx-ch4``, ``lien-fx``)."""
    key = token.strip().lower().replace("_", "-")
    return ALIASES.get(key)


def expand_instruments(instruments: str | Sequence[str] | None) -> list[str] | None:
    """If ``instruments`` is one pool alias, return that tuple; else None."""
    if instruments is None:
        return None
    if isinstance(instruments, str):
        parts = [p.strip() for p in instruments.split(",") if p.strip()]
    else:
        parts = [str(p).strip() for p in instruments if str(p).strip()]
    if len(parts) != 1:
        return None
    alias = resolve_alias(parts[0])
    return list(alias) if alias is not None else None
