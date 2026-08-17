"""Structured types for the agent orchestrator, proposal, and journal."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

PlayClass = Literal["join_trend", "fade_range", "breakout_watch"]
Side = Literal["long", "short", "none"]
Mode = Literal["signal", "paper"]
Action = Literal["wait", "log_setup", "pending_exec"]
FillStatus = Literal["pending", "filled_sim", "rejected"]
ExitStatus = Literal["stop", "target", "window_end"]


class Citation(BaseModel):
    source: str
    chunk_index: int
    distance: float | None = None


class Goal(BaseModel):
    instrument: str = "EUR_USD"
    granularity: str = "D"
    mode: Mode = "signal"
    count: int = 250
    from_time: str | None = None
    to_time: str | None = None
    risk_fraction: float = 0.02
    balance: float = 10_000.0
    open_risk_fraction: float = 0.0
    exposure_cap: float = 0.06
    value_per_price_unit: float = 1.0
    source_filter: str = "lien-fx"
    top_k: int = 5
    mt4: bool = False
    mt4_prefix: str = "sbox.regime."
    no_rag: bool = False
    no_llm: bool = False
    use_account: bool = False


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    return str(value)


_CONFIDENCE_WORDS = {
    "high": 0.8,
    "strong": 0.8,
    "medium": 0.5,
    "moderate": 0.5,
    "mid": 0.5,
    "low": 0.2,
    "weak": 0.2,
    "none": 0.0,
}


def _as_confidence(value: Any) -> float:
    """Map model confidence to a 0–1 float. Words like 'high' must not fail parse."""
    if value is None or value == "":
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        n = float(value)
    else:
        text = str(value).strip().lower().rstrip("%")
        if text in _CONFIDENCE_WORDS:
            return _CONFIDENCE_WORDS[text]
        try:
            n = float(text)
        except (TypeError, ValueError):
            return 0.0
        if n > 1.0 and n <= 100.0:
            n = n / 100.0
    return max(0.0, min(1.0, n))


class Proposal(BaseModel):
    thesis: str = ""
    play_class: PlayClass = "breakout_watch"
    side: Side = "none"
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    at_time: str | None = None
    confidence: float = 0.0
    citations: list[Citation] = Field(default_factory=list)
    notes: str = ""

    @field_validator("thesis", "notes", mode="before")
    @classmethod
    def _coerce_text(cls, value: Any) -> str:
        return _as_text(value)

    @field_validator("play_class", mode="before")
    @classmethod
    def _coerce_play_class(cls, value: Any) -> Any:
        if value is None or value == "":
            return "breakout_watch"
        return value

    @field_validator("side", mode="before")
    @classmethod
    def _coerce_side(cls, value: Any) -> Any:
        if value is None or value == "":
            return "none"
        mapping = {"buy": "long", "sell": "short"}
        if isinstance(value, str) and value.lower() in mapping:
            return mapping[value.lower()]
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, value: Any) -> float:
        return _as_confidence(value)


class RiskVerdict(BaseModel):
    ok: bool
    action: Action
    reasons: list[str] = Field(default_factory=list)
    r_planned: float | None = None
    size_units: float | None = None
    risk_fraction: float
    stop_distance: float | None = None


class ToolTrace(BaseModel):
    name: str
    latency_ms: float
    detail: str = ""


class RunRecord(BaseModel):
    run_id: str
    ts: str
    mode: Mode
    instrument: str
    granularity: str
    action: Action
    goal: Goal
    regime: dict[str, Any] = Field(default_factory=dict)
    proposal: Proposal | None = None
    risk: RiskVerdict
    citations: list[Citation] = Field(default_factory=list)
    tool_trace: list[ToolTrace] = Field(default_factory=list)
    error: str | None = None


class SimFill(BaseModel):
    run_id: str
    status: FillStatus
    fill_price: float | None = None
    ts: str
    note: str = ""
    exit_status: ExitStatus | None = None
    exit_price: float | None = None
    exit_ts: str | None = None
    r_realized: float | None = None


class PaperTrade(BaseModel):
    """One causal paper walk fill + outcome. Display / journal only."""

    run_id: str
    entry_index: int
    entry_time: str
    side: Side
    play_class: PlayClass
    entry: float
    stop: float
    target: float
    exit_index: int | None = None
    exit_time: str | None = None
    exit_price: float | None = None
    exit_status: ExitStatus | None = None
    r_realized: float | None = None
    reasons: list[str] = Field(default_factory=list)
