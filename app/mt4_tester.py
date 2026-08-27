"""MT4 Strategy Tester feed: bar export in, decision feed out.

Two-pass back-test plumbing for the entry engines. The ``SandboxTesterBridge``
EA (``InpMode=export``) writes every completed bar to ``bars.csv`` under the
tester sandbox; this module reads that, and ``agent.tester_backtest`` computes
rollover-peak MTF decisions and writes ``decisions.csv`` for the replay pass.

Feed lives at ``<tester/files>/sandbox002/<SYMBOL>_<TF>/{bars,decisions}.csv``.
Times on the wire are broker unix seconds (the tester clock); the engines run on
RFC3339 UTC strings, so this module converts on read/write. No orders here.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import mt4_bridge
from app.config import settings

INBOX_SUBDIR = "sandbox002"
BARS_NAME = "bars.csv"
DECISIONS_NAME = "decisions.csv"
DECISION_FIELDS = ("signal_time", "side", "entry", "stop", "target")

# HTF granularity -> resample bucket in seconds (UTC-calendar for D/W).
_DAY_SECONDS = 86_400


class TesterFeedError(RuntimeError):
    """Raised when the tester feed cannot be read or written."""


def tester_files_dir() -> Path:
    return Path(settings.mt4_tester_files_dir).expanduser()


def tester_dir(symbol: str, timeframe: str) -> Path:
    """``GBP_USD`` + ``H1`` -> ``<tester/files>/sandbox002/GBPUSD_H1``."""
    key = mt4_bridge.chart_key(symbol, timeframe)
    return tester_files_dir() / INBOX_SUBDIR / key


def bars_path(symbol: str, timeframe: str) -> Path:
    return tester_dir(symbol, timeframe) / BARS_NAME


def decisions_path(symbol: str, timeframe: str) -> Path:
    return tester_dir(symbol, timeframe) / DECISIONS_NAME


def unix_to_rfc3339(seconds: int) -> str:
    """Broker unix seconds -> engine-friendly RFC3339 UTC string."""
    dt = datetime.fromtimestamp(int(seconds), tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")


def rfc3339_to_unix(value: str) -> int:
    """RFC3339 (nanosecond ok) -> broker unix seconds."""
    parsed = mt4_bridge.parse_rfc3339_utc(value)
    if parsed is None:
        raise TesterFeedError(f"unparseable time: {value!r}")
    return int(parsed)


def read_bars_csv(path: Path | str) -> list[dict[str, Any]]:
    """Read ``bars.csv`` (``time,open,high,low,close,volume``) to bar dicts.

    ``time`` becomes an RFC3339 UTC string so the engines parse it; the raw
    broker seconds stay on ``time_unix``.
    """
    path = Path(path)
    if not path.exists():
        raise TesterFeedError(f"no bars file: {path}")
    bars: list[dict[str, Any]] = []
    with path.open(newline="") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if not row or row[0].strip() in ("", "time"):
                continue
            if len(row) < 6:
                raise TesterFeedError(f"short bars row: {row!r}")
            try:
                t = int(float(row[0]))
                bar = {
                    "time_unix": t,
                    "time": unix_to_rfc3339(t),
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": int(float(row[5])),
                    "complete": True,
                }
            except ValueError as exc:
                raise TesterFeedError(f"bad bars row {row!r}: {exc}") from exc
            bars.append(bar)
    bars.sort(key=lambda b: b["time_unix"])
    return bars


def _bucket_start(seconds: int, bucket: int) -> int:
    return (seconds // bucket) * bucket


def resample_bars(
    ltf_bars: list[dict[str, Any]], htf: str = "D"
) -> list[dict[str, Any]]:
    """Aggregate LTF bars into higher-TF bars by UTC-calendar bucket.

    Supports ``D`` (daily) and ``W`` (weekly, ISO Monday-anchored). open=first,
    high=max, low=min, close=last, volume=sum. Bars must carry ``time_unix``
    (as produced by :func:`read_bars_csv`).
    """
    key = htf.strip().upper()
    if key in ("D", "D1"):
        bucket = _DAY_SECONDS
    elif key in ("W", "W1"):
        bucket = 7 * _DAY_SECONDS
    else:
        raise TesterFeedError(f"unsupported resample target: {htf!r}")

    grouped: dict[int, list[dict[str, Any]]] = {}
    for bar in ltf_bars:
        sec = int(bar.get("time_unix") or rfc3339_to_unix(str(bar.get("time"))))
        if key in ("W", "W1"):
            # Anchor weeks to Monday 00:00 UTC (unix epoch is a Thursday).
            start = sec - ((sec // _DAY_SECONDS + 3) % 7) * _DAY_SECONDS
            start = _bucket_start(start, _DAY_SECONDS)
        else:
            start = _bucket_start(sec, bucket)
        grouped.setdefault(start, []).append(bar)

    out: list[dict[str, Any]] = []
    for start in sorted(grouped):
        members = sorted(
            grouped[start],
            key=lambda b: int(b.get("time_unix") or rfc3339_to_unix(str(b["time"]))),
        )
        out.append(
            {
                "time_unix": start,
                "time": unix_to_rfc3339(start),
                "open": float(members[0]["open"]),
                "high": max(float(m["high"]) for m in members),
                "low": min(float(m["low"]) for m in members),
                "close": float(members[-1]["close"]),
                "volume": sum(int(m.get("volume") or 0) for m in members),
                "complete": True,
            }
        )
    return out


def write_decisions_csv(
    path: Path | str, decisions: list[dict[str, Any]]
) -> Path:
    """Write the replay feed (``signal_time`` as broker unix seconds).

    Decisions are sorted ascending by signal time so the EA can consume them
    with a single forward index.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for d in decisions:
        rows.append(
            {
                "signal_time": rfc3339_to_unix(str(d["signal_time"])),
                "side": str(d["side"]),
                "entry": float(d["entry"]),
                "stop": float(d["stop"]),
                "target": float(d["target"]),
            }
        )
    rows.sort(key=lambda r: r["signal_time"])
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(DECISION_FIELDS))
        writer.writeheader()
        writer.writerows(rows)
    return path
