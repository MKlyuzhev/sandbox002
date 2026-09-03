"""Bounded Lien analysis graph. Nodes are code; one optional LLM propose step.

Prices come from Ch. 7 geometry (`agent/levels.py`), not from book chunks.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from agent import levels as levels_mod
from agent import policy, propose as propose_mod, retrieve as retrieve_mod
from agent.engines import registry as engine_registry
from agent.engines.base import EngineContext, EngineResult, result_to_proposal
from agent.journal import Journal
from agent.schema import Citation, EngineCandidate, Goal, Proposal, RunRecord, ToolTrace
from app import indicators, oanda_client, regime as regime_mod
from app.config import settings

logger = logging.getLogger("agent")

FetchBarsFn = Callable[[Goal], Awaitable[list[dict[str, Any]]]]
FetchAnalysesFn = Callable[[Goal, set[str]], Awaitable[dict[str, dict[str, Any]]]]
RetrieveFn = Callable[[str, int, str | None], Awaitable[list[dict[str, Any]]]]
ProposeFn = Callable[[dict[str, Any], list[dict[str, Any]], Goal], Proposal]
ClassifyFn = Callable[[list[dict[str, Any]]], dict[str, Any]]
ApplyMt4Fn = Callable[..., dict[str, Any]]
ApplyMt4TicketFn = Callable[..., dict[str, Any]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trace(name: str, started: float, detail: str = "") -> ToolTrace:
    return ToolTrace(
        name=name,
        latency_ms=round((time.perf_counter() - started) * 1000.0, 1),
        detail=detail[:240],
    )


def _citations_from_chunks(chunks: list[dict[str, Any]]) -> list[Citation]:
    out: list[Citation] = []
    for chunk in chunks:
        idx = chunk.get("chunk_index")
        source = chunk.get("source")
        if source is None or idx is None:
            continue
        out.append(
            Citation(
                source=str(source),
                chunk_index=int(idx),
                distance=chunk.get("distance"),
            )
        )
    return out


async def _default_fetch_bars(goal: Goal) -> list[dict[str, Any]]:
    count: int | None = goal.count
    if goal.from_time and goal.to_time:
        count = None
    payload = await oanda_client.get_candles(
        goal.instrument,
        granularity=goal.granularity,
        count=count,
        price="M",
        from_time=goal.from_time,
        to_time=goal.to_time,
    )
    return oanda_client.candles_to_bars(payload, prefer="mid")


async def _default_fetch_analyses(
    goal: Goal,
    granularities: set[str],
) -> dict[str, dict[str, Any]]:
    """Fetch + classify each requested granularity (network). Injectable in tests."""
    out: dict[str, dict[str, Any]] = {}
    for gran in granularities:
        sub_goal = goal.model_copy(update={"granularity": gran})
        bars = await _default_fetch_bars(sub_goal)
        analysis = regime_mod.analyze_bars(bars)
        analysis.setdefault("instrument", goal.instrument)
        analysis.setdefault("granularity", gran)
        out[gran] = analysis
    return out


def _candidates_from_results(results: list[EngineResult]) -> list[EngineCandidate]:
    return [
        EngineCandidate(
            engine=r.engine,
            chapter=r.chapter,
            firing=r.firing,
            signal=r.signal,
            play_class=r.play_class,
            confidence=r.confidence,
            reason=r.reason,
        )
        for r in results
    ]


async def _dispatch_engines(
    proposal: Proposal | None,
    analysis: dict[str, Any],
    goal: Goal,
    traces: list[ToolTrace],
    fetch_analyses_fn: FetchAnalysesFn | None,
    bars_injected: bool,
    used_bars: list[dict[str, Any]] | None = None,
) -> tuple[Proposal | None, list[EngineCandidate]]:
    """Pick a regime-matched engine and merge its ticket into the proposal.

    Falls back to Ch. 7 geometry when no engine matches the regime (e.g.
    breakout_watch), preserving prior behavior.
    """
    started = time.perf_counter()
    engines = engine_registry.select(analysis, goal)
    if not engines:
        traces.append(_trace("engines", started, "none matched -> ch7 geometry"))
        return levels_mod.apply_geometry(proposal, analysis, goal.instrument), []

    analyses: dict[str, dict[str, Any]] = {goal.granularity: analysis}
    extra = engine_registry.required_timeframes(engines, goal) - {goal.granularity}
    # Only reach for extra timeframes when we can do so deterministically: an
    # injected fetcher (tests) or live mode (bars not injected). This keeps
    # injected-bars runs offline; multi-TF engines simply do not fire.
    fetcher = fetch_analyses_fn or (None if bars_injected else _default_fetch_analyses)
    if extra and fetcher is not None:
        try:
            analyses.update(await fetcher(goal, extra))
        except Exception as exc:
            logger.info("engine extra-TF fetch failed: %s", exc)

    ctx = EngineContext(
        instrument=goal.instrument,
        goal=goal,
        analyses=analyses,
        bars={goal.granularity: used_bars} if used_bars else {},
    )
    chosen, results = engine_registry.run_and_pick(engines, ctx)
    cands = _candidates_from_results(results)
    if chosen is None:
        detail = f"no firing; {len(engines)} engine(s)"
        traces.append(_trace("engines", started, detail))
        fallback = results[0] if results else None
        if fallback is None:
            return proposal, cands
        return result_to_proposal(proposal, fallback, analysis), cands

    traces.append(
        _trace(
            "engines",
            started,
            f"chosen=ch{chosen.chapter} {chosen.engine} conf={chosen.confidence}",
        )
    )
    return result_to_proposal(proposal, chosen, analysis), cands


async def _maybe_use_account(goal: Goal) -> Goal:
    if not goal.use_account:
        return goal
    summary = await oanda_client.get_account_summary()
    raw = summary.get("NAV") or summary.get("balance")
    if raw is None:
        return goal
    return goal.model_copy(update={"balance": float(raw)})


async def run(
    goal: Goal,
    *,
    bars: list[dict[str, Any]] | None = None,
    fetch_bars: FetchBarsFn | None = None,
    fetch_analyses_fn: FetchAnalysesFn | None = None,
    retrieve_fn: RetrieveFn | None = None,
    propose_fn: ProposeFn | None = None,
    classify_fn: ClassifyFn | None = None,
    apply_mt4_fn: ApplyMt4Fn | None = None,
    apply_mt4_ticket_fn: ApplyMt4TicketFn | None = None,
    journal: Journal | None = None,
) -> RunRecord:
    """Run the analysis graph. Inject callables to avoid network in tests."""
    traces: list[ToolTrace] = []
    chunks: list[dict[str, Any]] = []
    proposal: Proposal | None = None
    engine_candidates: list[EngineCandidate] = []
    error: str | None = None
    analysis: dict[str, Any] = {}
    used_bars: list[dict[str, Any]] = bars or []

    logger.info(
        "run %s %s mode=%s no_rag=%s no_llm=%s",
        goal.instrument,
        goal.granularity,
        goal.mode,
        goal.no_rag,
        goal.no_llm,
    )

    if goal.use_account:
        t0 = time.perf_counter()
        logger.info("fetching OANDA account summary...")
        try:
            goal = await _maybe_use_account(goal)
            traces.append(_trace("account", t0, f"balance={goal.balance}"))
            logger.info("account balance=%s", goal.balance)
        except Exception as exc:
            traces.append(_trace("account", t0, str(exc)))
            error = f"account: {exc}"
            logger.info("account failed: %s", exc)

    if error is None:
        t1 = time.perf_counter()
        try:
            if bars is None:
                logger.info(
                    "fetching %s %s candles (count=%s)...",
                    goal.instrument,
                    goal.granularity,
                    goal.count,
                )
                fetcher = fetch_bars or _default_fetch_bars
                used_bars = await fetcher(goal)
            traces.append(_trace("candles", t1, f"bars={len(used_bars)}"))
            logger.info("candles: %s bars", len(used_bars))
        except Exception as exc:
            traces.append(_trace("candles", t1, str(exc)))
            error = f"candles: {exc}"
            logger.info("candles failed: %s", exc)

    if error is None:
        t2 = time.perf_counter()
        logger.info("classifying Lien regime...")
        try:
            classifier = classify_fn or regime_mod.analyze_bars
            analysis = classifier(used_bars)
            analysis.setdefault("instrument", goal.instrument)
            analysis.setdefault("granularity", goal.granularity)
            traces.append(
                _trace(
                    "regime",
                    t2,
                    f"{analysis.get('regime')} waning={analysis.get('trend_waning')}",
                )
            )
            logger.info(
                "regime=%s direction=%s waning=%s plays=%s",
                analysis.get("regime"),
                analysis.get("direction"),
                analysis.get("trend_waning"),
                analysis.get("allowed_play_classes"),
            )
        except (indicators.IndicatorError, Exception) as exc:
            traces.append(_trace("regime", t2, str(exc)))
            error = f"regime: {exc}"
            logger.info("regime failed: %s", exc)

    if error is None and goal.mt4:
        t3 = time.perf_counter()
        logger.info("drawing MT4 regime overlay...")
        try:
            from app import mt4_bridge

            drawer = apply_mt4_fn or mt4_bridge.apply_regime
            mt4_result = drawer(
                analysis,
                used_bars,
                goal.instrument,
                goal.granularity,
                prefix=goal.mt4_prefix,
            )
            analysis["mt4"] = {k: v for k, v in mt4_result.items() if k != "analysis"}
            traces.append(
                _trace("mt4", t3, str(mt4_result.get("ok", mt4_result.get("error"))))
            )
            logger.info("mt4 overlay: %s", mt4_result.get("ok", mt4_result.get("error")))
        except Exception as exc:
            traces.append(_trace("mt4", t3, str(exc)))
            logger.info("mt4 failed: %s", exc)

    waning = bool(analysis.get("trend_waning")) if analysis else True

    if analysis.get("trend_waning"):
        logger.info("trend_waning: skipping retrieve/propose")

    if error is None and not waning and not goal.no_rag:
        t4 = time.perf_counter()
        logger.info(
            "retrieving %s (embed model %s)...",
            goal.source_filter or "corpus",
            settings.ollama_embed_model,
        )
        try:
            retriever = retrieve_fn or (
                lambda q, k, src: retrieve_mod.search_knowledge(q, top_k=k, source=src)
            )
            query = retrieve_mod.default_query(analysis)
            chunks = await retriever(query, goal.top_k, goal.source_filter or None)
            traces.append(_trace("retrieve", t4, f"chunks={len(chunks)} q={query}"))
            logger.info("retrieved %s chunk(s)", len(chunks))
        except Exception as exc:
            traces.append(_trace("retrieve", t4, str(exc)))
            logger.info("retrieve failed: %s", exc)

    if error is None and not waning:
        t5 = time.perf_counter()
        try:
            if propose_fn is not None:
                logger.info("proposing (injected)...")
                proposal = propose_fn(analysis, chunks, goal)
            elif goal.no_llm:
                logger.info("proposing skeleton (--no-llm)")
                proposal = propose_mod.skeleton_proposal(analysis)
            else:
                logger.info(
                    "proposing with %s (Ollama chat; blank pause usually means VRAM load)...",
                    settings.ollama_llm_model,
                )
                proposal = await propose_mod.llm_propose(analysis, chunks, goal)
            proposal, engine_candidates = await _dispatch_engines(
                proposal,
                analysis,
                goal,
                traces,
                fetch_analyses_fn,
                bars_injected=bars is not None,
                used_bars=used_bars,
            )
            traces.append(
                _trace(
                    "propose",
                    t5,
                    f"{proposal.play_class} {proposal.side}" if proposal else "none",
                )
            )
            logger.info(
                "proposal %s %s entry=%s",
                proposal.play_class if proposal else None,
                proposal.side if proposal else None,
                proposal.entry if proposal else None,
            )
        except Exception as exc:
            traces.append(_trace("propose", t5, str(exc)))
            error = f"propose: {exc}"
            proposal = None
            logger.info("propose failed: %s", exc)

    citations = list(proposal.citations) if proposal else _citations_from_chunks(chunks)
    if proposal is not None and not proposal.citations:
        proposal = proposal.model_copy(update={"citations": citations})

    verdict = policy.evaluate(analysis, proposal, goal)
    if error and verdict.ok:
        verdict = policy.evaluate({}, None, goal)
    logger.info(
        "policy action=%s ok=%s reasons=%s",
        verdict.action,
        verdict.ok,
        "; ".join(verdict.reasons) or "-",
    )

    if goal.mt4:
        t6 = time.perf_counter()
        try:
            from app import mt4_bridge

            has_ticket = (
                verdict.ok
                and proposal is not None
                and proposal.entry is not None
                and proposal.stop is not None
                and proposal.target is not None
            )
            if has_ticket:
                drawer = apply_mt4_ticket_fn or mt4_bridge.apply_ticket
                ticket_result = drawer(
                    goal.instrument,
                    goal.granularity,
                    proposal.entry,
                    proposal.stop,
                    proposal.target,
                    side=proposal.side,
                    prefix=mt4_bridge.TICKET_PREFIX,
                    at_time=analysis.get("last_time"),
                )
                analysis["mt4_ticket"] = {
                    k: v for k, v in ticket_result.items() if k != "analysis"
                }
                traces.append(
                    _trace(
                        "mt4_ticket",
                        t6,
                        str(ticket_result.get("ok", ticket_result.get("error"))),
                    )
                )
                logger.info(
                    "mt4 ticket: %s",
                    ticket_result.get("ok", ticket_result.get("error")),
                )
            else:
                clearer = mt4_bridge.clear_layer
                clear_result = clearer(
                    mt4_bridge.TICKET_PREFIX,
                    symbol=goal.instrument,
                    timeframe=goal.granularity,
                )
                traces.append(
                    _trace(
                        "mt4_ticket",
                        t6,
                        "cleared"
                        if clear_result.get("ok")
                        else str(clear_result.get("error", "clear skipped")),
                    )
                )
        except Exception as exc:
            traces.append(_trace("mt4_ticket", t6, str(exc)))
            logger.info("mt4 ticket failed: %s", exc)

    record = RunRecord(
        run_id=uuid.uuid4().hex,
        ts=_now(),
        mode=goal.mode,
        instrument=goal.instrument,
        granularity=goal.granularity,
        action=verdict.action,
        goal=goal,
        regime=analysis,
        proposal=proposal,
        risk=verdict,
        citations=citations,
        tool_trace=traces,
        engine_candidates=engine_candidates,
        error=error,
    )
    if journal is not None:
        journal.append_run(record)
        logger.info("journaled %s → %s", record.run_id, journal.path)
    return record
