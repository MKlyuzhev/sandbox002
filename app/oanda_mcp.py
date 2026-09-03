"""OANDA (practice) MCP server for research and paper-journal tools.

Market-data, regime/entry peeks, and planner jobs (`scan_regimes`, `run_graph`,
`run_walk`). No broker or MT4 order placement. Paper mode only queues sqlite
`pending_exec`. MT4 helpers draw chart objects via a file inbox
(see ``app.mt4_bridge``).

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
    """MT4 bridge status: inbox path/writable, live charts list, newest
    chart's heartbeat, last command id, and last EA error.

    Call this before drawing. Attach SandboxChartBridge.mq4 to each chart
    you care about (AutoTrading can stay off — objects only, no orders).
    ``charts`` lists every folder with a heartbeat; ``ea_ok`` is true if
    any heartbeat is fresh.
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
    instrument: str,
    timeframe: str,
    names: list[str] | None = None,
    prefix: str = "",
) -> dict:
    """Delete chart objects by exact names and/or name prefix (e.g. sbox.formation.).

    Writes to that chart's inbox only (``sandbox002/<SYMBOL>_<TF>/``).
    """
    return mt4_bridge.delete_objects(
        names=names or [],
        prefix=prefix,
        symbol=instrument,
        timeframe=timeframe,
    )


@mcp.tool()
async def mt4_clear_layer(
    instrument: str,
    timeframe: str,
    prefix: str = "sbox.",
) -> dict:
    """Remove sandbox chart objects whose names start with prefix on one chart."""
    return mt4_bridge.clear_layer(
        prefix=prefix,
        symbol=instrument,
        timeframe=timeframe,
    )


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


def _parse_engines(engines: str) -> list[int] | None:
    text = (engines or "").strip()
    if not text:
        return None
    return [int(x) for x in text.split(",") if x.strip()]


@mcp.tool()
async def scan_regimes(
    instruments: str = "",
    granularity: str = "D",
    count: int = 250,
    from_time: str = "",
    to_time: str = "",
    drop_waning: bool = True,
    play_class: str = "",
) -> dict:
    """Classify Lien Ch.7 regime for a small universe (sequential).

    Default universe is the seven USD majors if ``instruments`` is empty.
    At most 12 names. ``drop_waning`` (default true) puts waning pairs in
    ``dropped`` with reason ``trend_waning``. Optional ``play_class`` keeps
    only pairs whose ``allowed_play_classes`` include it. Compact rows — do
    not recompute ADX/Bollinger in the model. Research only; no orders.
    """
    from agent.scan import ScanError, scan_regimes as _scan

    try:
        return await _scan(
            instruments,
            granularity=granularity,
            count=count,
            from_time=from_time,
            to_time=to_time,
            drop_waning=drop_waning,
            play_class=play_class,
        )
    except ScanError as exc:
        return {"error": str(exc)}


@mcp.tool()
async def run_graph(
    instrument: str,
    granularity: str = "D",
    ltf_granularity: str = "H1",
    engines: str = "",
    mode: str = "signal",
    count: int = 250,
    from_time: str = "",
    to_time: str = "",
    risk_fraction: float = 0.02,
    balance: float = 10_000.0,
    exposure_cap: float = 0.06,
    no_llm: bool = True,
    no_rag: bool = False,
    mt4: bool = False,
    use_account: bool = False,
    source: str = "lien-fx",
    top_k: int = 5,
    no_journal: bool = False,
) -> dict:
    """Run the bounded Lien graph (regime → engines → policy → journal).

    Same path as ``python -m agent.run``. Policy cannot be skipped. Default
    ``mode=signal`` journals ``log_setup`` / ``wait`` only; ``paper`` queues
    sqlite ``pending_exec`` (stub fills, no broker). ``no_llm`` defaults true
    so prices stay on engines. Returns ``RunRecord`` JSON including
    ``engine_candidates``. Research only; no orders.
    """
    from agent.graph import run as graph_run
    from agent.journal import Journal
    from agent.schema import Goal

    if mode not in ("signal", "paper"):
        return {"error": f"mode must be signal or paper; got {mode!r}"}
    try:
        engine_list = _parse_engines(engines)
    except ValueError as exc:
        return {"error": f"engines: {exc}"}
    goal = Goal(
        instrument=instrument,
        granularity=granularity,
        ltf_granularity=ltf_granularity,
        engines=engine_list,
        mode=mode,  # type: ignore[arg-type]
        count=count,
        from_time=from_time or None,
        to_time=to_time or None,
        risk_fraction=risk_fraction,
        balance=balance,
        exposure_cap=exposure_cap,
        mt4=mt4,
        no_rag=no_rag,
        no_llm=no_llm,
        use_account=use_account,
        source_filter=source,
        top_k=top_k,
    )
    journal = None if no_journal else Journal()
    record = await graph_run(goal, journal=journal)
    return record.model_dump(mode="json")


@mcp.tool()
async def run_walk(
    kind: str,
    instrument: str,
    from_time: str,
    to_time: str,
    chapter: int = 0,
    granularity: str = "D",
    ltf_granularity: str = "H1",
    lookback: int = 250,
    fill_mode: str = "close",
    balance: float = 10_000.0,
    risk_fraction: float = 0.02,
    exposure_cap: float = 0.06,
    value_per_price_unit: float = 1.0,
    no_journal: bool = False,
) -> dict:
    """Causal paper walk (ch7 / mtf / lien). Not the MT4 Strategy Tester.

    Requires ``from_time`` and ``to_time`` (RFC3339). Lien ``chapter`` is
    9, 13, 14, or 16. Unencoded chapters return an error. Response is
    ``walk_id``, ``equity``, ``trade_count``, and a truncated trade list.
    Can be expensive (OANDA history). Research only; no broker orders.
    """
    from agent.walk_jobs import (
        WalkJobError,
        WalkRuntime,
        run_walk as _run_walk,
        walk_job_error_payload,
    )

    if fill_mode not in ("close", "rest"):
        return {"error": f"fill_mode must be close or rest; got {fill_mode!r}"}
    ch = chapter if chapter else None
    try:
        return await _run_walk(
            kind,
            instrument,
            from_time,
            to_time,
            chapter=ch,
            granularity=granularity,
            ltf_granularity=ltf_granularity,
            lookback=lookback,
            fill_mode=fill_mode,  # type: ignore[arg-type]
            balance=balance,
            risk_fraction=risk_fraction,
            exposure_cap=exposure_cap,
            value_per_price_unit=value_per_price_unit,
            no_journal=no_journal,
        )
    except WalkJobError as exc:
        return walk_job_error_payload(exc)
    except WalkRuntime as exc:
        return walk_job_error_payload(exc)


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
async def entry_mtf(
    instrument: str,
    htf_granularity: str = "D",
    ltf_granularity: str = "H1",
    htf_count: int = 250,
    ltf_count: int = 250,
    rsi_os: float = 30.0,
    rsi_ob: float = 70.0,
    buffer_pips: int = 10,
) -> dict:
    """Lien Ch.8 Multiple Time Frame entry signal (deterministic).

    Higher timeframe sets trend direction; the lower timeframe times the entry
    on an RSI pullback: buy dips (rsi<=rsi_os) in an uptrend, sell rallies
    (rsi>=rsi_ob) in a downtrend. Only signals with the higher-TF trend and
    never when the higher TF is trend_waning. Indicators/regime are computed in
    code (Ch.7 filter) — do not recompute them in the model.

    Returns a signal (long|short|none), reason, htf/ltf summaries, a
    entry/stop/2R ticket when aligned (else null), and lien-fx citations. On too
    few bars on either timeframe, returns an ``error`` field. Research only; no
    orders.
    """
    from app import indicators
    from agent.engines import mtf

    try:
        _htf_bars, htf_analysis = await _analyze_regime(
            instrument, htf_granularity, htf_count, "", ""
        )
        _ltf_bars, ltf_analysis = await _analyze_regime(
            instrument, ltf_granularity, ltf_count, "", ""
        )
    except indicators.IndicatorError as exc:
        return {
            "error": str(exc),
            "instrument": instrument,
            "htf_granularity": htf_granularity,
            "ltf_granularity": ltf_granularity,
        }

    return mtf.mtf_signal(
        htf_analysis,
        ltf_analysis,
        instrument,
        rsi_os=rsi_os,
        rsi_ob=rsi_ob,
        buffer_pips=buffer_pips,
        htf_granularity=htf_granularity,
        ltf_granularity=ltf_granularity,
    )


@mcp.tool()
async def entry_dbb(
    instrument: str,
    granularity: str = "D",
    count: int = 250,
    buffer_pips: int = 10,
) -> dict:
    """Lien Ch.9 Double Bollinger Bands entry signal (deterministic).

    Keys off a close crossing the 1sigma band: join_trend (break out into the
    outer zone) or fade_range (reclaim back through 1sigma). Only fires with the
    Ch.7 regime's allowed play classes and never when trend_waning. Indicators
    are computed in code — do not recompute them in the model.

    Returns a signal (long|short|none), play_class, reason, snapshot summary, a
    2R ticket when aligned (else null), and lien-fx citations. Research only; no
    orders.
    """
    from app import indicators
    from agent.engines import dbb

    try:
        _bars, analysis = await _analyze_regime(instrument, granularity, count, "", "")
    except indicators.IndicatorError as exc:
        return {"error": str(exc), "instrument": instrument, "granularity": granularity}

    return dbb.dbb_signal(
        analysis,
        instrument,
        buffer_pips=buffer_pips,
        granularity=granularity,
    )


@mcp.tool()
async def entry_lien(
    chapter: int,
    instrument: str,
    granularity: str = "D",
    ltf_granularity: str = "H1",
    count: int = 250,
    ltf_count: int = 250,
    buffer_pips: int = 10,
    probe_pips: int = 15,
) -> dict:
    """Lien Ch.13 / 14 / 16 entry signal (deterministic).

    Chapter 13 (fader): daily ADX<20 + H1 probe ≥15 pips beyond prior day H/L,
    fade. Chapter 14 (20-day breakout): rebreak after a ≥2-day pullback, not
    first touch. Chapter 16 (perfect order): SMA stack intact, ADX rising,
    pulse when stack age is exactly 5. Other chapters return an error (10/11/12/15
    are not encoded yet; 8 is entry_mtf; 9 is entry_dbb).

    Always after the Ch.7 filter. Research only; no orders.
    """
    from app import indicators
    from agent.lien_chapters import ENTRY_LIEN_CHAPTERS, entry_lien_error
    from agent.engines import breakout20, fader, perfect_order

    if chapter not in ENTRY_LIEN_CHAPTERS:
        return {
            "error": entry_lien_error(chapter),
            "chapter": chapter,
            "instrument": instrument,
        }

    try:
        bars, analysis = await _analyze_regime(instrument, granularity, count, "", "")
    except indicators.IndicatorError as exc:
        return {
            "error": str(exc),
            "chapter": chapter,
            "instrument": instrument,
            "granularity": granularity,
        }

    if chapter == 13:
        try:
            _ltf_bars, ltf_analysis = await _analyze_regime(
                instrument, ltf_granularity, ltf_count, "", ""
            )
        except indicators.IndicatorError as exc:
            return {
                "error": str(exc),
                "chapter": chapter,
                "instrument": instrument,
                "ltf_granularity": ltf_granularity,
            }
        return fader.fader_signal(
            analysis,
            ltf_analysis,
            instrument,
            buffer_pips=buffer_pips,
            probe_pips=probe_pips,
            htf_granularity=granularity,
            ltf_granularity=ltf_granularity,
        )
    if chapter == 14:
        return breakout20.breakout20_signal(
            analysis,
            instrument,
            buffer_pips=buffer_pips,
            granularity=granularity,
            bars=bars,
        )
    return perfect_order.perfect_order_signal(
        analysis,
        instrument,
        buffer_pips=buffer_pips,
        granularity=granularity,
        bars=bars,
    )


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
