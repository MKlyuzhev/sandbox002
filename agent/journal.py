"""SQLite decision journal (runs + stub fills)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from agent.schema import RunRecord, SimFill

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = _REPO_ROOT / "data" / "journal" / "runs.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    mode TEXT NOT NULL,
    instrument TEXT NOT NULL,
    granularity TEXT NOT NULL,
    action TEXT NOT NULL,
    goal_json TEXT NOT NULL,
    regime_json TEXT NOT NULL,
    proposal_json TEXT,
    risk_json TEXT NOT NULL,
    citations_json TEXT,
    trace_json TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    status TEXT NOT NULL,
    fill_price REAL,
    ts TEXT NOT NULL,
    note TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    return conn


class Journal:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_DB_PATH

    def append_run(self, record: RunRecord) -> RunRecord:
        payload = record.model_dump(mode="json")
        with _connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    id, ts, mode, instrument, granularity, action,
                    goal_json, regime_json, proposal_json, risk_json,
                    citations_json, trace_json, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.ts,
                    record.mode,
                    record.instrument,
                    record.granularity,
                    record.action,
                    json.dumps(payload["goal"]),
                    json.dumps(payload["regime"], default=str),
                    json.dumps(payload["proposal"]) if payload["proposal"] else None,
                    json.dumps(payload["risk"]),
                    json.dumps(payload["citations"]),
                    json.dumps(payload["tool_trace"]),
                    record.error,
                ),
            )
            if record.action == "pending_exec":
                conn.execute(
                    """
                    INSERT INTO fills (run_id, status, fill_price, ts, note)
                    VALUES (?, 'pending', NULL, ?, ?)
                    """,
                    (record.run_id, _now(), "queued by orchestrator"),
                )
            conn.commit()
        return record

    def get_run(self, run_id: str) -> RunRecord | None:
        with _connect(self.path) as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return _row_to_record(row)

    def list_runs(
        self,
        limit: int = 50,
        instrument: str | None = None,
        action: str | None = None,
    ) -> list[RunRecord]:
        """Newest-first run list. ``limit`` is clamped to 1–500."""
        limit = max(1, min(int(limit), 500))
        clauses = []
        params: list[object] = []
        if instrument:
            clauses.append("instrument = ?")
            params.append(instrument)
        if action:
            clauses.append("action = ?")
            params.append(action)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        sql = f"SELECT * FROM runs {where} ORDER BY ts DESC LIMIT ?"
        with _connect(self.path) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_record(row) for row in rows]

    def list_pending(self) -> list[RunRecord]:
        with _connect(self.path) as conn:
            rows = conn.execute(
                """
                SELECT runs.* FROM fills
                JOIN runs ON runs.id = fills.run_id
                WHERE fills.status = 'pending'
                ORDER BY fills.id ASC
                """
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def record_fill(self, fill: SimFill) -> SimFill:
        with _connect(self.path) as conn:
            cur = conn.execute(
                """
                UPDATE fills
                SET status = ?, fill_price = ?, ts = ?, note = ?
                WHERE run_id = ? AND status = 'pending'
                """,
                (fill.status, fill.fill_price, fill.ts, fill.note, fill.run_id),
            )
            if cur.rowcount == 0:
                conn.execute(
                    """
                    INSERT INTO fills (run_id, status, fill_price, ts, note)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (fill.run_id, fill.status, fill.fill_price, fill.ts, fill.note),
                )
            conn.commit()
        return fill


def _row_to_record(row: sqlite3.Row) -> RunRecord:
    proposal = json.loads(row["proposal_json"]) if row["proposal_json"] else None
    return RunRecord(
        run_id=row["id"],
        ts=row["ts"],
        mode=row["mode"],
        instrument=row["instrument"],
        granularity=row["granularity"],
        action=row["action"],
        goal=json.loads(row["goal_json"]),
        regime=json.loads(row["regime_json"]),
        proposal=proposal,
        risk=json.loads(row["risk_json"]),
        citations=json.loads(row["citations_json"] or "[]"),
        tool_trace=json.loads(row["trace_json"] or "[]"),
        error=row["error"],
    )
