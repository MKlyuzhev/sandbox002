"""Chapter ids for encoded Lien engines (this iteration: 13, 14, 16 + 8/9)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent.engines import breakout20, dbb, fader, mtf, perfect_order

# MCP ``entry_lien`` implements these only.
ENTRY_LIEN_CHAPTERS: frozenset[int] = frozenset({13, 14, 16})
DEFERRED_CHAPTERS: frozenset[int] = frozenset({10, 11, 12, 15})

CHAPTER_TO_ENGINE: dict[int, str] = {
    8: "mtf",
    9: "dbb",
    13: "fader",
    14: "breakout20",
    16: "perfect_order",
}

ENGINE_TO_CHAPTER: dict[str, int] = {v: k for k, v in CHAPTER_TO_ENGINE.items()}

# Single-TF one-shot events (tester / event_walk).
EVENT_ENGINES: frozenset[str] = frozenset({"dbb", "breakout20", "perfect_order"})

SignalFn = Callable[..., dict[str, Any]]

EVENT_SIGNAL: dict[str, SignalFn] = {
    "dbb": dbb.dbb_signal,
    "breakout20": breakout20.breakout20_signal,
    "perfect_order": perfect_order.perfect_order_signal,
}


def resolve_engine(*, chapter: int | None, engine: str | None) -> str:
    """Map ``--chapter`` / ``--engine`` to a registry engine name."""
    if chapter is not None and engine:
        mapped = CHAPTER_TO_ENGINE.get(chapter)
        if mapped is None:
            raise ValueError(entry_lien_error(chapter))
        if mapped != engine:
            raise ValueError(
                f"--chapter {chapter} is engine {mapped!r}, not {engine!r}"
            )
        return mapped
    if chapter is not None:
        mapped = CHAPTER_TO_ENGINE.get(chapter)
        if mapped is None:
            raise ValueError(entry_lien_error(chapter))
        return mapped
    if engine:
        if engine not in ENGINE_TO_CHAPTER and engine != "ch7_geometry":
            raise ValueError(f"unknown engine {engine!r}")
        return engine
    return "mtf"


def entry_lien_error(chapter: int) -> str:
    if chapter == 8:
        return "Chapter 8 is the entry_mtf tool, not entry_lien."
    if chapter == 9:
        return "Chapter 9 is the entry_dbb tool, not entry_lien."
    if chapter in DEFERRED_CHAPTERS:
        return (
            f"Chapter {chapter} is not encoded yet (deferred: news, session clock, "
            "or breakout_watch paper policy). This iteration covers chapters 13, "
            "14, and 16 only."
        )
    return (
        f"Unsupported chapter {chapter}. entry_lien implements 13 (fader), "
        "14 (20-day breakout), and 16 (perfect order)."
    )
