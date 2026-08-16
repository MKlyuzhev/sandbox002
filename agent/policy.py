"""Hard regime and risk gates. The model cannot skip this node."""

from __future__ import annotations

from agent.schema import Action, Goal, Proposal, RiskVerdict
from app import risk as risk_lib

MIN_R = 2.0


def _wait(goal: Goal, reasons: list[str], **extra) -> RiskVerdict:
    return RiskVerdict(
        ok=False,
        action="wait",
        reasons=reasons,
        risk_fraction=goal.risk_fraction,
        **extra,
    )


def _pass_action(goal: Goal, play_class: str) -> Action:
    if play_class == "breakout_watch":
        return "log_setup"
    if goal.mode == "paper":
        return "pending_exec"
    return "log_setup"


def evaluate(
    regime: dict,
    proposal: Proposal | None,
    goal: Goal,
) -> RiskVerdict:
    """Return a verdict. Failures always map to action=wait."""
    if regime.get("trend_waning"):
        return _wait(goal, ["trend_waning: do not aggress"])

    allowed = list(regime.get("allowed_play_classes") or [])
    if proposal is None:
        return _wait(goal, ["no proposal"])

    if proposal.play_class not in allowed:
        return _wait(
            goal,
            [
                f"play_class {proposal.play_class!r} not in allowed_play_classes {allowed}"
            ],
        )

    missing: list[str] = []
    if proposal.side == "none":
        missing.append("side")
    if proposal.entry is None:
        missing.append("entry")
    if proposal.stop is None:
        missing.append("stop")
    if missing:
        return _wait(goal, [f"missing {', '.join(missing)}"])

    if proposal.target is None:
        return _wait(goal, ["missing target (need planned R)"])

    entry, stop, target = proposal.entry, proposal.stop, proposal.target
    if proposal.side == "long" and not (stop < entry):
        return _wait(goal, ["long requires stop below entry"])
    if proposal.side == "short" and not (stop > entry):
        return _wait(goal, ["short requires stop above entry"])

    try:
        r_planned = risk_lib.r_multiple(entry, stop, target)
    except risk_lib.RiskError as exc:
        return _wait(goal, [str(exc)])

    if r_planned < MIN_R:
        return _wait(
            goal,
            [f"planned R {r_planned:.3f} < {MIN_R:.1f}"],
            r_planned=r_planned,
            stop_distance=abs(entry - stop),
        )

    stop_distance = abs(entry - stop)
    try:
        size_units = risk_lib.position_size(
            goal.balance,
            goal.risk_fraction,
            stop_distance,
            value_per_price_unit=goal.value_per_price_unit,
        )
    except risk_lib.RiskError as exc:
        return _wait(
            goal,
            [str(exc)],
            r_planned=r_planned,
            stop_distance=stop_distance,
        )

    if not risk_lib.max_exposure_ok(
        goal.open_risk_fraction, goal.risk_fraction, goal.exposure_cap
    ):
        return _wait(
            goal,
            [
                "open risk "
                f"{goal.open_risk_fraction} + new {goal.risk_fraction} "
                f"> cap {goal.exposure_cap}"
            ],
            r_planned=r_planned,
            size_units=size_units,
            stop_distance=stop_distance,
        )

    action = _pass_action(goal, proposal.play_class)
    return RiskVerdict(
        ok=True,
        action=action,
        reasons=[],
        r_planned=r_planned,
        size_units=size_units,
        risk_fraction=goal.risk_fraction,
        stop_distance=stop_distance,
    )
