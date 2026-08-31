"""Shared contract for Lien entry engines.

An engine consumes already-computed ``regime.analyze_bars`` output (one dict per
timeframe) and returns an ``EngineResult``. The graph selects engines by regime,
runs them, picks the highest-confidence firing signal, and merges it into the
proposal via ``result_to_proposal``. Engines never recompute indicators, never
place orders, and always run after the Ch. 7 regime filter.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from agent.schema import Citation, Goal, PlayClass, Proposal, Side


class EngineContext(BaseModel):
    """Inputs an engine needs: instrument, goal, analyses, and optional OHLC."""

    instrument: str
    goal: Goal
    analyses: dict[str, dict[str, Any]] = Field(default_factory=dict)
    bars: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)

    def analysis(self, granularity: str) -> dict[str, Any] | None:
        return self.analyses.get(granularity)

    def bars_for(self, granularity: str) -> list[dict[str, Any]] | None:
        return self.bars.get(granularity)


class EngineResult(BaseModel):
    """One engine's verdict for a single decision."""

    engine: str
    chapter: int
    signal: Side = "none"
    play_class: PlayClass = "breakout_watch"
    ticket: dict[str, Any] | None = None
    reason: str = ""
    confidence: float = 0.0
    citations: list[Citation] = Field(default_factory=list)

    @property
    def firing(self) -> bool:
        """A firing result has a directional signal and a concrete ticket."""
        return self.signal in ("long", "short") and self.ticket is not None


@runtime_checkable
class Engine(Protocol):
    """Deterministic entry engine. Implementations must be pure over the context."""

    chapter: int
    name: str
    play_classes: tuple[PlayClass, ...]

    def granularities(self, goal: Goal) -> tuple[str, ...]:
        """Timeframes this engine needs for ``goal`` (first is the primary)."""
        ...

    def signal(self, ctx: EngineContext) -> EngineResult: ...


def _merge_notes(existing: str, extra: str) -> str:
    text = (existing or "").strip()
    return f"{text}; {extra}" if text else extra


def result_to_proposal(
    base: Proposal | None,
    result: EngineResult,
    analysis: dict[str, Any],
) -> Proposal:
    """Merge a chosen engine result into the proposal.

    Overwrites play_class/side/entry/stop/target from the engine (prices are
    never kept from the model) and appends citations, preserving thesis/notes.
    Mirrors the merge in ``agent.levels.apply_geometry``.
    """
    proposal = base or Proposal()
    at_time = analysis.get("last_time") or proposal.at_time
    citations = list(proposal.citations) or list(result.citations)

    if not result.firing:
        note = f"ch{result.chapter} {result.engine}: {result.reason or 'no ticket'}"
        return proposal.model_copy(
            update={
                "play_class": result.play_class,
                "side": "none",
                "entry": None,
                "stop": None,
                "target": None,
                "at_time": at_time,
                "engine": result.engine,
                "chapter": result.chapter,
                "citations": citations,
                "notes": _merge_notes(proposal.notes, note),
            }
        )

    ticket = result.ticket or {}
    note = (
        f"ch{result.chapter} {result.engine}: {ticket.get('side')} "
        f"entry={ticket.get('entry_name')} stop={ticket.get('stop_name')} "
        f"conf={result.confidence}"
    )
    return proposal.model_copy(
        update={
            "play_class": result.play_class,
            "side": ticket.get("side", result.signal),
            "entry": ticket.get("entry"),
            "stop": ticket.get("stop"),
            "target": ticket.get("target"),
            "confidence": result.confidence,
            "at_time": at_time,
            "engine": result.engine,
            "chapter": result.chapter,
            "citations": citations,
            "notes": _merge_notes(proposal.notes, note),
        }
    )
