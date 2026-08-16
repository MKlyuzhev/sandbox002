"""Whitelist subprocess runner: agent.run / agent.executor only. No shell."""

from __future__ import annotations

import asyncio
import re
import signal
import sys
from collections import deque
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

_REPO_ROOT = Path(__file__).resolve().parent.parent
_GRANULARITIES = frozenset(
    {
        "S5",
        "S10",
        "S15",
        "S30",
        "M1",
        "M5",
        "M15",
        "M30",
        "H1",
        "H2",
        "H3",
        "H4",
        "H6",
        "H8",
        "H12",
        "D",
        "W",
        "M",
    }
)
ALLOWED_CMDS = ("agent.run", "agent.executor")
_RFC3339 = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$"
)
_SOURCE = re.compile(r"^[a-zA-Z0-9_-]+$")
_PREFIX = re.compile(r"^sbox\.[a-zA-Z0-9._-]*$")

# UI groups: primary always shown; run/executor live under More.
SCHEMA_FIELDS: list[dict[str, Any]] = [
    {
        "name": "cmd",
        "type": "enum",
        "choices": list(ALLOWED_CMDS),
        "group": "primary",
        "label": "cmd",
    },
    {"name": "instrument", "type": "string", "group": "primary", "label": "instrument"},
    {"name": "granularity", "type": "string", "group": "primary", "label": "granularity"},
    {
        "name": "mode",
        "type": "enum",
        "choices": ["signal", "paper"],
        "group": "primary",
        "label": "mode",
    },
    {"name": "no_llm", "type": "bool", "group": "primary", "label": "no-llm", "flag": "--no-llm"},
    {"name": "no_rag", "type": "bool", "group": "primary", "label": "no-rag", "flag": "--no-rag"},
    {"name": "mt4", "type": "bool", "group": "primary", "label": "mt4", "flag": "--mt4"},
    {"name": "count", "type": "int", "group": "run", "label": "count", "flag": "--count", "placeholder": "250"},
    {"name": "from_time", "type": "string", "group": "run", "label": "from", "flag": "--from", "placeholder": "2024-01-01T00:00:00Z"},
    {"name": "to_time", "type": "string", "group": "run", "label": "to", "flag": "--to", "placeholder": "2024-01-01T00:00:00Z"},
    {"name": "balance", "type": "float", "group": "run", "label": "balance", "flag": "--balance", "placeholder": "10000"},
    {"name": "risk_fraction", "type": "float", "group": "run", "label": "risk-fraction", "flag": "--risk-fraction", "placeholder": "0.02"},
    {"name": "exposure_cap", "type": "float", "group": "run", "label": "exposure-cap", "flag": "--exposure-cap", "placeholder": "0.06"},
    {"name": "use_account", "type": "bool", "group": "run", "label": "use-account", "flag": "--use-account"},
    {"name": "source", "type": "string", "group": "run", "label": "source", "flag": "--source", "placeholder": "lien-fx"},
    {"name": "top_k", "type": "int", "group": "run", "label": "top-k", "flag": "--top-k", "placeholder": "5"},
    {"name": "mt4_prefix", "type": "string", "group": "run", "label": "mt4-prefix", "flag": "--mt4-prefix", "placeholder": "sbox.regime."},
    {"name": "quiet", "type": "bool", "group": "run", "label": "quiet", "flag": "--quiet"},
    {"name": "no_journal", "type": "bool", "group": "run", "label": "no-journal", "flag": "--no-journal"},
    {"name": "watch", "type": "bool", "group": "executor", "label": "watch", "flag": "--watch"},
    {"name": "interval", "type": "float", "group": "executor", "label": "interval", "flag": "--interval", "placeholder": "5"},
]


class JobError(ValueError):
    """Invalid job spec or busy runner."""


def _blank_none(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


class JobSpec(BaseModel):
    cmd: Literal["agent.run", "agent.executor"] = "agent.run"
    instrument: str = "EUR_USD"
    granularity: str = "D"
    mode: Literal["signal", "paper"] = "signal"
    no_llm: bool = False
    no_rag: bool = False
    mt4: bool = False
    count: int | None = Field(default=None, ge=1, le=5000)
    from_time: str | None = None
    to_time: str | None = None
    balance: float | None = Field(default=None, gt=0)
    risk_fraction: float | None = Field(default=None, gt=0, le=1)
    exposure_cap: float | None = Field(default=None, gt=0, le=1)
    use_account: bool = False
    source: str | None = None
    top_k: int | None = Field(default=None, ge=1, le=50)
    mt4_prefix: str | None = None
    quiet: bool = False
    no_journal: bool = False
    watch: bool = False
    interval: float | None = Field(default=None, ge=0.5, le=3600)

    @field_validator("instrument")
    @classmethod
    def _instrument(cls, value: str) -> str:
        text = value.strip()
        if not text.replace("_", "").isalnum() or "_" not in text:
            raise ValueError("instrument must look like GBP_USD")
        return text

    @field_validator("granularity")
    @classmethod
    def _granularity(cls, value: str) -> str:
        text = value.strip().upper()
        if text not in _GRANULARITIES:
            raise ValueError(f"unsupported granularity {value!r}")
        return text

    @field_validator("from_time", "to_time", "source", "mt4_prefix", mode="before")
    @classmethod
    def _empty_str(cls, value: Any) -> Any:
        return _blank_none(value)

    @field_validator("from_time", "to_time")
    @classmethod
    def _rfc3339(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _RFC3339.match(value.strip()):
            raise ValueError("must be RFC3339 (e.g. 2024-01-01T00:00:00Z)")
        return value.strip()

    @field_validator("source")
    @classmethod
    def _source(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not _SOURCE.match(text):
            raise ValueError("source must be alphanumeric, underscore, or hyphen")
        return text

    @field_validator("mt4_prefix")
    @classmethod
    def _prefix(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not _PREFIX.match(text):
            raise ValueError("mt4-prefix must start with sbox.")
        return text


def job_schema() -> dict[str, Any]:
    defaults = JobSpec().model_dump()
    fields = []
    for item in SCHEMA_FIELDS:
        row = dict(item)
        row["default"] = defaults.get(item["name"])
        fields.append(row)
    return {"cmds": list(ALLOWED_CMDS), "fields": fields}


def build_argv(spec: JobSpec, python: str | None = None) -> list[str]:
    """Return argv for a whitelist command. Never uses a shell."""
    py = python or sys.executable
    if spec.cmd not in ALLOWED_CMDS:
        raise JobError(f"cmd not allowed: {spec.cmd!r}")
    if spec.cmd == "agent.executor":
        argv = [py, "-m", "agent.executor"]
        if spec.watch:
            argv.append("--watch")
            if spec.interval is not None:
                argv.extend(["--interval", str(spec.interval)])
        else:
            argv.append("--once")
        return argv
    argv = [
        py,
        "-m",
        "agent.run",
        "--instrument",
        spec.instrument,
        "--granularity",
        spec.granularity,
        "--mode",
        spec.mode,
    ]
    if spec.no_llm:
        argv.append("--no-llm")
    if spec.no_rag:
        argv.append("--no-rag")
    if spec.mt4:
        argv.append("--mt4")
    if spec.count is not None:
        argv.extend(["--count", str(spec.count)])
    if spec.from_time:
        argv.extend(["--from", spec.from_time])
    if spec.to_time:
        argv.extend(["--to", spec.to_time])
    if spec.balance is not None:
        argv.extend(["--balance", str(spec.balance)])
    if spec.risk_fraction is not None:
        argv.extend(["--risk-fraction", str(spec.risk_fraction)])
    if spec.exposure_cap is not None:
        argv.extend(["--exposure-cap", str(spec.exposure_cap)])
    if spec.use_account:
        argv.append("--use-account")
    if spec.source:
        argv.extend(["--source", spec.source])
    if spec.top_k is not None:
        argv.extend(["--top-k", str(spec.top_k)])
    if spec.mt4_prefix:
        argv.extend(["--mt4-prefix", spec.mt4_prefix])
    if spec.quiet:
        argv.append("--quiet")
    if spec.no_journal:
        argv.append("--no-journal")
    return argv


class JobManager:
    """At most one subprocess; log lines fan out to SSE subscribers."""

    def __init__(self, cwd: Path | None = None, python: str | None = None) -> None:
        self.cwd = cwd or _REPO_ROOT
        self.python = python or sys.executable
        self._proc: asyncio.subprocess.Process | None = None
        self._buffer: deque[str] = deque(maxlen=2000)
        self._subs: list[asyncio.Queue[str | None]] = []
        self._spec: JobSpec | None = None
        self._reader: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def snapshot(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "cmd": self._spec.cmd if self._spec else None,
            "argv": build_argv(self._spec, self.python) if self._spec else None,
        }

    def subscribe(self) -> asyncio.Queue[str | None]:
        q: asyncio.Queue[str | None] = asyncio.Queue()
        self._subs.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str | None]) -> None:
        if q in self._subs:
            self._subs.remove(q)

    def replay(self) -> list[str]:
        return list(self._buffer)

    def _emit(self, line: str) -> None:
        self._buffer.append(line)
        for q in list(self._subs):
            q.put_nowait(line)

    async def start(self, spec: JobSpec) -> dict[str, Any]:
        if self.running:
            raise JobError("a job is already running")
        argv = build_argv(spec, self.python)
        self._spec = spec
        self._buffer.clear()
        start_line = "$ " + " ".join(argv)
        self._emit(start_line)
        self._proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(self.cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._reader = asyncio.create_task(self._pump())
        return {"ok": True, "pid": self._proc.pid, "argv": argv}

    async def stop(self) -> dict[str, Any]:
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return {"ok": True, "running": False}
        proc.send_signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), timeout=8.0)
        except TimeoutError:
            proc.kill()
            await proc.wait()
        return {"ok": True, "running": False, "returncode": proc.returncode}

    async def _pump(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\n")
                self._emit(line)
        finally:
            code = await proc.wait()
            self._emit(f"[exit {code}]")
            for q in list(self._subs):
                q.put_nowait(None)
            self._proc = None
            self._reader = None
