"""SQLite decision journal (runs + stub fills)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from agent.schema import EngineCandidate, RunRecord, SimFill

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
    error TEXT,
    walk_id TEXT
);

CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    status TEXT NOT NULL,
    fill_price REAL,
    ts TEXT NOT NULL,
    note TEXT,
    exit_status TEXT,
    exit_price REAL,
    exit_ts TEXT,
    r_realized REAL,
    walk_id TEXT,
    pnl REAL,
    equity_after REAL,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);
"""

_RUN_EXTRAS = (("walk_id", "TEXT"), ("candidates_json", "TEXT"))
_FILL_EXTRAS = (
    ("exit_status", "TEXT"),
    ("exit_price", "REAL"),
    ("exit_ts", "TEXT"),
    ("r_realized", "REAL"),
    ("walk_id", "TEXT"),
    ("pnl", "REAL"),
    ("equity_after", "REAL"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    run_cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    for name, typ in _RUN_EXTRAS:
        if name not in run_cols:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {typ}")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(fills)")}
    for name, typ in _FILL_EXTRAS:
        if name not in cols:
            conn.execute(f"ALTER TABLE fills ADD COLUMN {name} {typ}")


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_schema(conn)
    return conn


class Journal:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_DB_PATH

    def append_run(self, record: RunRecord, *, queue_fill: bool = True) -> RunRecord:
        payload = record.model_dump(mode="json")
        with _connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    id, ts, mode, instrument, granularity, action,
                    goal_json, regime_json, proposal_json, risk_json,
                    citations_json, trace_json, error, walk_id,
                    candidates_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    record.walk_id,
                    json.dumps(payload.get("engine_candidates") or []),
                ),
            )
            if queue_fill and record.action == "pending_exec":
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
        walk_id: str | None = None,
    ) -> list[RunRecord]:
        """Newest writes first (SQLite ``rowid``). Walk ``ts`` is the decision bar,
        which can be years earlier than wall-clock snapshot runs."""
        limit = max(1, min(int(limit), 500))
        clauses = []
        params: list[object] = []
        if instrument:
            clauses.append("instrument = ?")
            params.append(instrument)
        if action:
            clauses.append("action = ?")
            params.append(action)
        if walk_id:
            clauses.append("walk_id = ?")
            params.append(walk_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        sql = f"SELECT * FROM runs {where} ORDER BY rowid DESC LIMIT ?"
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
                SET status = ?, fill_price = ?, ts = ?, note = ?,
                    exit_status = ?, exit_price = ?, exit_ts = ?, r_realized = ?,
                    walk_id = ?, pnl = ?, equity_after = ?
                WHERE run_id = ? AND status = 'pending'
                """,
                (
                    fill.status,
                    fill.fill_price,
                    fill.ts,
                    fill.note,
                    fill.exit_status,
                    fill.exit_price,
                    fill.exit_ts,
                    fill.r_realized,
                    fill.walk_id,
                    fill.pnl,
                    fill.equity_after,
                    fill.run_id,
                ),
            )
            if cur.rowcount == 0:
                conn.execute(
                    """
                    INSERT INTO fills (
                        run_id, status, fill_price, ts, note,
                        exit_status, exit_price, exit_ts, r_realized,
                        walk_id, pnl, equity_after
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fill.run_id,
                        fill.status,
                        fill.fill_price,
                        fill.ts,
                        fill.note,
                        fill.exit_status,
                        fill.exit_price,
                        fill.exit_ts,
                        fill.r_realized,
                        fill.walk_id,
                        fill.pnl,
                        fill.equity_after,
                    ),
                )
            conn.commit()
        return fill

    def record_exit(self, fill: SimFill) -> SimFill:
        """Attach stop/target/window_end to an already filled_sim walk trade."""
        with _connect(self.path) as conn:
            cur = conn.execute(
                """
                UPDATE fills
                SET exit_status = ?, exit_price = ?, exit_ts = ?, r_realized = ?,
                    note = ?, walk_id = ?, pnl = ?, equity_after = ?
                WHERE run_id = ? AND status = 'filled_sim'
                """,
                (
                    fill.exit_status,
                    fill.exit_price,
                    fill.exit_ts,
                    fill.r_realized,
                    fill.note,
                    fill.walk_id,
                    fill.pnl,
                    fill.equity_after,
                    fill.run_id,
                ),
            )
            if cur.rowcount == 0:
                raise ValueError(f"no filled_sim row for run_id {fill.run_id}")
            conn.commit()
        return fill

    def get_fill(self, run_id: str) -> SimFill | None:
        with _connect(self.path) as conn:
            row = conn.execute(
                "SELECT * FROM fills WHERE run_id = ? ORDER BY id DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return _row_to_fill(row)

    def list_fills_for_walk(self, walk_id: str) -> list[SimFill]:
        with _connect(self.path) as conn:
            rows = conn.execute(
                """
                SELECT * FROM fills
                WHERE walk_id = ?
                ORDER BY id ASC
                """,
                (walk_id,),
            ).fetchall()
        return [_row_to_fill(row) for row in rows]


def _row_to_record(row: sqlite3.Row) -> RunRecord:
    proposal = json.loads(row["proposal_json"]) if row["proposal_json"] else None
    keys = set(row.keys())
    raw_cands = []
    if "candidates_json" in keys and row["candidates_json"]:
        raw_cands = json.loads(row["candidates_json"])
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
        engine_candidates=[EngineCandidate.model_validate(c) for c in raw_cands],
        error=row["error"],
        walk_id=row["walk_id"] if "walk_id" in keys else None,
    )


def _row_to_fill(row: sqlite3.Row) -> SimFill:
    keys = set(row.keys())
    return SimFill(
        run_id=row["run_id"],
        status=row["status"],
        fill_price=row["fill_price"],
        ts=row["ts"],
        note=row["note"] or "",
        exit_status=row["exit_status"] if "exit_status" in keys else None,
        exit_price=row["exit_price"] if "exit_price" in keys else None,
        exit_ts=row["exit_ts"] if "exit_ts" in keys else None,
        r_realized=row["r_realized"] if "r_realized" in keys else None,
        walk_id=row["walk_id"] if "walk_id" in keys else None,
        pnl=row["pnl"] if "pnl" in keys else None,
        equity_after=row["equity_after"] if "equity_after" in keys else None,
    )
