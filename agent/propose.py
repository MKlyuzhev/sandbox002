"""One Ollama chat call → structured Proposal JSON."""

from __future__ import annotations

import json
import re
from typing import Any

from agent.schema import Citation, Goal, Proposal
from app import ollama_client

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)

SYSTEM = (
    "You map a Lien Ch.7 regime snapshot and book excerpts to a trade proposal. "
    "Return ONLY a JSON object with keys: thesis, play_class, side, entry, stop, "
    "target, confidence, citations, notes. "
    "play_class must be one of join_trend, fade_range, breakout_watch and should "
    "match allowed_play_classes. "
    "side, entry, stop, and target will be overwritten by Ch.7 geometry in code "
    "from the indicator snapshot — set side to none and entry/stop/target to null. "
    "citations is a list of {source, chunk_index} from the excerpts. "
    "Do not invent prices, risk_reversals, or implied vol. "
    "Do not recompute ADX, Bollinger, SMA, RSI, stochastics, or MACD. "
    "notes must be a single string, not a list. "
    "confidence must be a number between 0 and 1 (not words like high). "
    "Research only; do not instruct live orders."
)


class ProposeError(ValueError):
    """Raised when the model output cannot be parsed as a Proposal."""


def strip_think(text: str) -> str:
    return _THINK_BLOCK.sub("", text).strip()


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = strip_think(text)
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        data = json.loads(cleaned[start : end + 1])
        if isinstance(data, dict):
            return data
    raise ProposeError("model output is not a JSON object")


def parse_proposal(
    text: str,
    allowed_play_classes: list[str] | None = None,
) -> Proposal:
    data = _extract_json_object(text)
    raw_cites = data.get("citations") or []
    citations: list[Citation] = []
    for item in raw_cites:
        if not isinstance(item, dict):
            continue
        source = item.get("source")
        idx = item.get("chunk_index")
        if source is None or idx is None:
            continue
        citations.append(
            Citation(
                source=str(source),
                chunk_index=int(idx),
                distance=item.get("distance"),
            )
        )
    data["citations"] = [c.model_dump() for c in citations]
    if not data.get("play_class"):
        plays = [p for p in (allowed_play_classes or []) if p]
        data["play_class"] = plays[0] if plays else "breakout_watch"
    if not data.get("thesis"):
        data["thesis"] = "model omitted thesis"
    try:
        return Proposal.model_validate(data)
    except Exception as exc:
        raise ProposeError(f"proposal failed validation: {exc}") from exc


def skeleton_proposal(regime: dict[str, Any]) -> Proposal:
    plays = list(regime.get("allowed_play_classes") or ["breakout_watch"])
    return Proposal(
        thesis="no-llm skeleton; Ch.7 geometry fills prices from the snapshot",
        play_class=plays[0],
        side="none",
        notes="--no-llm",
    )


def _excerpt_block(chunks: list[dict[str, Any]], limit: int = 4) -> str:
    parts: list[str] = []
    for chunk in chunks[:limit]:
        idx = chunk.get("chunk_index")
        source = chunk.get("source")
        text = (chunk.get("text") or "")[:800]
        parts.append(f"[{source} #{idx}]\n{text}")
    return "\n\n".join(parts) if parts else "(no excerpts)"


async def llm_propose(
    regime: dict[str, Any],
    chunks: list[dict[str, Any]],
    goal: Goal,
) -> Proposal:
    snap = {k: regime.get(k) for k in (
        "regime",
        "direction",
        "trend_waning",
        "allowed_play_classes",
        "confidence",
        "notes",
        "last_close",
        "last_time",
        "risk_reversals",
        "implied_vol",
    )}
    user = (
        f"Instrument: {goal.instrument} {goal.granularity}\n"
        f"Regime JSON:\n{json.dumps(snap, default=str)}\n\n"
        f"Excerpts:\n{_excerpt_block(chunks)}\n\n"
        "Propose JSON now. Leave entry/stop/target null; geometry fills them."
    )
    raw = await ollama_client.chat(SYSTEM + "\n\n/no_think", user, json_mode=True)
    return parse_proposal(
        raw,
        allowed_play_classes=list(regime.get("allowed_play_classes") or []),
    )
