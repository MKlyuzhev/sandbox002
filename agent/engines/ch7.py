"""Ch. 7 geometry as a fallback engine.

Wraps ``agent.levels`` snapshot geometry (last close entry, 10-bar high/low
buffer stop, 2R target) behind the engine interface so the graph can treat it
uniformly. It is the generic fallback: it fires for any ``join_trend`` /
``fade_range`` regime, so specialized chapter engines are preferred by
confidence when they also fire.
"""

from __future__ import annotations

from agent import levels as levels_mod
from agent.engines.base import EngineContext, EngineResult
from agent.schema import Goal, PlayClass


class Ch7Engine:
    chapter = 7
    name = "ch7_geometry"
    play_classes: tuple[PlayClass, ...] = ("join_trend", "fade_range")

    def granularities(self, goal: Goal) -> tuple[str, ...]:
        return (goal.granularity,)

    # Fallback discount: a firing specialized engine (Ch. 8+) outranks Ch. 7
    # geometry at equal regime strength.
    FALLBACK_WEIGHT = 0.5

    def signal(self, ctx: EngineContext) -> EngineResult:
        analysis = ctx.analysis(ctx.goal.granularity) or {}
        allowed = [
            p for p in (analysis.get("allowed_play_classes") or [])
            if p in self.play_classes
        ]
        regime_conf = float(analysis.get("confidence") or 0.0)
        confidence = round(self.FALLBACK_WEIGHT * regime_conf, 3)
        for play_class in allowed:
            ticket = levels_mod.plan_ticket(analysis, play_class, ctx.instrument)
            if ticket is not None:
                return EngineResult(
                    engine=self.name,
                    chapter=self.chapter,
                    signal=ticket["side"],
                    play_class=play_class,
                    ticket=ticket,
                    reason=f"ch7 geometry {play_class}",
                    confidence=confidence,
                )
        play_class = allowed[0] if allowed else "breakout_watch"
        return EngineResult(
            engine=self.name,
            chapter=self.chapter,
            signal="none",
            play_class=play_class,
            reason="ch7 geometry: no ticket",
            confidence=confidence,
        )
