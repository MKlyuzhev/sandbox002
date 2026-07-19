"""Deterministic risk and position-sizing math.

Pure functions only: no LLM, no network, no I/O. This is the seam the roadmap
calls out (section 4) -- an agent's model chooses *which* rule and *how to
interpret*, but the numbers are computed here in code. Both a future autonomous
loop and an optional MCP wrapper can import these directly.

All monetary values are in the account's home currency; prices are in an
instrument's quote units. "Fraction" arguments are decimals (0.01 == 1%).
"""

from __future__ import annotations


class RiskError(ValueError):
    """Raised when inputs violate a risk invariant (e.g. non-positive stop)."""


def position_size(
    account_balance: float,
    risk_fraction: float,
    stop_distance_price: float,
    value_per_price_unit: float = 1.0,
) -> float:
    """Return position size (units) risking a fixed fraction of the account.

    Args:
        account_balance: Account equity in home currency.
        risk_fraction: Fraction of the account to risk on this trade
            (e.g. 0.01 for 1%). Must be in (0, 1].
        stop_distance_price: Distance between entry and stop, in price units
            (e.g. 0.0025 for 25 pips on a 4-decimal FX pair). Must be > 0.
        value_per_price_unit: Home-currency P&L per 1 unit of position per 1.0
            of price movement. For many USD-quoted FX pairs sized in units,
            this is ~1.0; adjust for contract/pip specifics.

    Returns:
        Position size in units (float, unrounded). Callers round to the
        instrument's tradeable increment.
    """
    if account_balance <= 0:
        raise RiskError("account_balance must be positive")
    if not 0 < risk_fraction <= 1:
        raise RiskError("risk_fraction must be in (0, 1]")
    if stop_distance_price <= 0:
        raise RiskError("stop_distance_price must be positive")
    if value_per_price_unit <= 0:
        raise RiskError("value_per_price_unit must be positive")

    risk_amount = account_balance * risk_fraction
    risk_per_unit = stop_distance_price * value_per_price_unit
    return risk_amount / risk_per_unit


def r_multiple(entry: float, stop: float, exit_price: float) -> float:
    """Return the realized R-multiple of a trade.

    R is profit/loss expressed in units of initial risk (entry-to-stop
    distance). A trade exited at +2R made twice its risk. Direction is inferred:
    stop below entry => long; stop above entry => short.

    Raises:
        RiskError: if entry == stop (undefined risk).
    """
    risk = entry - stop
    if risk == 0:
        raise RiskError("entry and stop must differ (risk cannot be zero)")
    # For a long, risk > 0 and profit = exit - entry.
    # For a short, risk < 0 and profit = entry - exit; dividing by risk
    # (negative) yields the correct sign in both cases.
    return (exit_price - entry) / risk


def expectancy(win_rate: float, avg_win_r: float, avg_loss_r: float) -> float:
    """Return per-trade expectancy in R.

    Args:
        win_rate: Probability of a winning trade, in [0, 1].
        avg_win_r: Average win size in R (positive, e.g. 2.0).
        avg_loss_r: Average loss size in R as a positive magnitude (e.g. 1.0).

    Returns:
        Expected R per trade: win_rate * avg_win_r - (1 - win_rate) * avg_loss_r.
        Positive means a positive-expectancy system.
    """
    if not 0 <= win_rate <= 1:
        raise RiskError("win_rate must be in [0, 1]")
    if avg_win_r < 0 or avg_loss_r < 0:
        raise RiskError("avg_win_r and avg_loss_r must be non-negative magnitudes")
    return win_rate * avg_win_r - (1 - win_rate) * avg_loss_r


def max_exposure_ok(
    open_risk_fraction: float,
    new_risk_fraction: float,
    cap: float,
) -> bool:
    """Return whether adding a new trade keeps total open risk within a cap.

    Args:
        open_risk_fraction: Fraction of the account already at risk across open
            positions.
        new_risk_fraction: Fraction the prospective trade would add.
        cap: Maximum allowed total open-risk fraction (e.g. 0.06 for 6%).

    Returns:
        True if open_risk_fraction + new_risk_fraction <= cap.
    """
    if open_risk_fraction < 0 or new_risk_fraction < 0 or cap < 0:
        raise RiskError("risk fractions and cap must be non-negative")
    return (open_risk_fraction + new_risk_fraction) <= cap
