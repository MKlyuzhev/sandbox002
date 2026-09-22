"""SQLite store for trader chart marks, fills, reviews, and definitions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = _REPO_ROOT / "data" / "journal" / "trader.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    ts INTEGER NOT NULL,
    objects_json TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    ticket INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    type TEXT NOT NULL,
    lots REAL,
    open_price REAL,
    open_time INTEGER,
    sl REAL,
    tp REAL,
    magic INTEGER,
    comment TEXT,
    close_price REAL,
    close_time INTEGER,
    profit REAL,
    swap REAL,
    commission REAL,
    sl_open REAL,
    tp_open REAL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket INTEGER NOT NULL,
    object_name TEXT NOT NULL,
    role TEXT,
    reason TEXT
);
CREATE TABLE IF NOT EXISTS episodes (
    id TEXT PRIMARY KEY,
    ticket INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    open_time INTEGER,
    close_time INTEGER,
    snapshot_id INTEGER,
    objects_json TEXT,
    brief TEXT
);
CREATE TABLE IF NOT EXISTS reviews (
    episode_id TEXT PRIMARY KEY,
    model_fit REAL,
    entry_quality REAL,
    exit_quality REAL,
    reasons_json TEXT,
    diagnostics_json TEXT,
    brief TEXT,
    FOREIGN KEY (episode_id) REFERENCES episodes(id)
);
CREATE TABLE IF NOT EXISTS rules (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    cluster_key TEXT,
    text TEXT NOT NULL,
    failure_mode TEXT,
    created_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS definitions (
    term TEXT PRIMARY KEY,
    granularity TEXT,
    notes TEXT,
    updated_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT,
    timeframe TEXT,
    ts INTEGER,
    kind TEXT,
    name TEXT,
    ticket INTEGER,
    seq INTEGER,
    UNIQUE (symbol, timeframe, seq)
);
"""


def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


class TraderStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_DB_PATH

    def upsert_snapshot(
        self,
        symbol: str,
        timeframe: str,
        ts: int,
        objects: list[dict[str, Any]],
    ) -> int:
        blob = json.dumps(objects, default=str, sort_keys=True)
        digest = hashlib.sha1(blob.encode("utf-8")).hexdigest()
        with _connect(self.path) as conn:
            row = conn.execute(
                "SELECT id FROM snapshots WHERE symbol=? AND timeframe=? AND hash=?",
                (symbol, timeframe, digest),
            ).fetchone()
            if row:
                return int(row["id"])
            cur = conn.execute(
                """
                INSERT INTO snapshots (symbol, timeframe, ts, objects_json, hash)
                VALUES (?, ?, ?, ?, ?)
                """,
                (symbol, timeframe, ts, blob, digest),
            )
            conn.commit()
            return int(cur.lastrowid)

    def latest_snapshot(
        self, symbol: str, timeframe: str, at_ts: int | None = None
    ) -> dict[str, Any] | None:
        sql = "SELECT * FROM snapshots WHERE symbol=? AND timeframe=?"
        args: list[Any] = [symbol, timeframe]
        if at_ts is not None:
            sql += " AND ts<=?"
            args.append(at_ts)
        sql += " ORDER BY ts DESC, id DESC LIMIT 1"
        with _connect(self.path) as conn:
            row = conn.execute(sql, args).fetchone()
        if row is None:
            return None
        return dict(row)

    def upsert_order(self, order: dict[str, Any], *, status: str) -> None:
        ticket = int(order["ticket"])
        with _connect(self.path) as conn:
            existing = conn.execute(
                "SELECT sl_open, tp_open FROM orders WHERE ticket=?", (ticket,)
            ).fetchone()
            sl_open = existing["sl_open"] if existing else order.get("sl")
            tp_open = existing["tp_open"] if existing else order.get("tp")
            conn.execute(
                """
                INSERT INTO orders (
                    ticket, symbol, type, lots, open_price, open_time,
                    sl, tp, magic, comment, close_price, close_time,
                    profit, swap, commission, sl_open, tp_open, status
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(ticket) DO UPDATE SET
                    sl=excluded.sl, tp=excluded.tp, lots=excluded.lots,
                    close_price=excluded.close_price, close_time=excluded.close_time,
                    profit=excluded.profit, swap=excluded.swap,
                    commission=excluded.commission, status=excluded.status
                """,
                (
                    ticket,
                    order.get("symbol"),
                    order.get("type"),
                    order.get("lots"),
                    order.get("open_price"),
                    order.get("open_time"),
                    order.get("sl"),
                    order.get("tp"),
                    order.get("magic"),
                    order.get("comment"),
                    order.get("close_price"),
                    order.get("close_time"),
                    order.get("profit"),
                    order.get("swap"),
                    order.get("commission"),
                    sl_open,
                    tp_open,
                    status,
                ),
            )
            conn.commit()

    def get_order(self, ticket: int) -> dict[str, Any] | None:
        with _connect(self.path) as conn:
            row = conn.execute(
                "SELECT * FROM orders WHERE ticket=?", (ticket,)
            ).fetchone()
        return dict(row) if row else None

    def list_orders(
        self, symbol: str | None = None, include_closed: bool = True
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM orders"
        args: list[Any] = []
        clauses: list[str] = []
        if symbol:
            clauses.append("symbol=?")
            args.append(symbol)
        if not include_closed:
            clauses.append("status='open'")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY open_time DESC"
        with _connect(self.path) as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def replace_links(self, ticket: int, links: list[dict[str, Any]]) -> None:
        with _connect(self.path) as conn:
            conn.execute("DELETE FROM links WHERE ticket=?", (ticket,))
            for link in links:
                conn.execute(
                    "INSERT INTO links (ticket, object_name, role, reason) VALUES (?,?,?,?)",
                    (
                        ticket,
                        link.get("object_name"),
                        link.get("role"),
                        link.get("reason"),
                    ),
                )
            conn.commit()

    def links_for(self, ticket: int) -> list[dict[str, Any]]:
        with _connect(self.path) as conn:
            rows = conn.execute(
                "SELECT * FROM links WHERE ticket=?", (ticket,)
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_episode(self, episode: dict[str, Any]) -> None:
        with _connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO episodes (
                    id, ticket, symbol, timeframe, open_time, close_time,
                    snapshot_id, objects_json, brief
                ) VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    close_time=excluded.close_time,
                    objects_json=excluded.objects_json,
                    brief=excluded.brief,
                    snapshot_id=excluded.snapshot_id
                """,
                (
                    episode["id"],
                    episode["ticket"],
                    episode["symbol"],
                    episode["timeframe"],
                    episode.get("open_time"),
                    episode.get("close_time"),
                    episode.get("snapshot_id"),
                    episode.get("objects_json"),
                    episode.get("brief"),
                ),
            )
            conn.commit()

    def get_episode(self, episode_id: str) -> dict[str, Any] | None:
        with _connect(self.path) as conn:
            row = conn.execute(
                "SELECT * FROM episodes WHERE id=?", (episode_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_episodes(
        self,
        symbol: str | None = None,
        from_ts: int | None = None,
        to_ts: int | None = None,
        has_fill: bool | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM episodes"
        clauses: list[str] = []
        args: list[Any] = []
        if symbol:
            clauses.append("symbol=?")
            args.append(symbol)
        if from_ts is not None:
            clauses.append("open_time>=?")
            args.append(from_ts)
        if to_ts is not None:
            clauses.append("open_time<=?")
            args.append(to_ts)
        if has_fill is True:
            clauses.append("close_time IS NOT NULL AND close_time>0")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY open_time DESC"
        with _connect(self.path) as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def upsert_review(self, review: dict[str, Any]) -> None:
        with _connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO reviews (
                    episode_id, model_fit, entry_quality, exit_quality,
                    reasons_json, diagnostics_json, brief
                ) VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(episode_id) DO UPDATE SET
                    model_fit=excluded.model_fit,
                    entry_quality=excluded.entry_quality,
                    exit_quality=excluded.exit_quality,
                    reasons_json=excluded.reasons_json,
                    diagnostics_json=excluded.diagnostics_json,
                    brief=excluded.brief
                """,
                (
                    review["episode_id"],
                    review.get("model_fit"),
                    review.get("entry_quality"),
                    review.get("exit_quality"),
                    json.dumps(review.get("reasons") or []),
                    json.dumps(review.get("diagnostics") or {}),
                    review.get("brief"),
                ),
            )
            conn.commit()

    def get_review(self, episode_id: str) -> dict[str, Any] | None:
        with _connect(self.path) as conn:
            row = conn.execute(
                "SELECT * FROM reviews WHERE episode_id=?", (episode_id,)
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["reasons"] = json.loads(data.pop("reasons_json") or "[]")
        data["diagnostics"] = json.loads(data.pop("diagnostics_json") or "{}")
        return data

    def list_reviews(
        self, episode_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        with _connect(self.path) as conn:
            if episode_ids:
                q = ",".join("?" * len(episode_ids))
                rows = conn.execute(
                    f"SELECT * FROM reviews WHERE episode_id IN ({q})",
                    episode_ids,
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM reviews").fetchall()
        out = []
        for row in rows:
            data = dict(row)
            data["reasons"] = json.loads(data.pop("reasons_json") or "[]")
            data["diagnostics"] = json.loads(data.pop("diagnostics_json") or "{}")
            out.append(data)
        return out

    def upsert_rule(self, rule: dict[str, Any]) -> None:
        with _connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO rules (id, status, cluster_key, text, failure_mode, created_ts)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status, text=excluded.text
                """,
                (
                    rule["id"],
                    rule.get("status") or "hypothesis",
                    rule.get("cluster_key"),
                    rule["text"],
                    rule.get("failure_mode"),
                    rule.get("created_ts") or _now_ts(),
                ),
            )
            conn.commit()

    def list_rules(self, status: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM rules"
        args: list[Any] = []
        if status:
            sql += " WHERE status=?"
            args.append(status)
        sql += " ORDER BY created_ts DESC"
        with _connect(self.path) as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def set_definition(self, term: str, granularity: str = "", notes: str = "") -> dict[str, Any]:
        payload = {
            "term": term.strip(),
            "granularity": granularity,
            "notes": notes,
            "updated_ts": _now_ts(),
        }
        with _connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO definitions (term, granularity, notes, updated_ts)
                VALUES (?,?,?,?)
                ON CONFLICT(term) DO UPDATE SET
                    granularity=excluded.granularity,
                    notes=excluded.notes,
                    updated_ts=excluded.updated_ts
                """,
                (payload["term"], granularity, notes, payload["updated_ts"]),
            )
            conn.commit()
        return payload

    def get_definitions(self) -> list[dict[str, Any]]:
        with _connect(self.path) as conn:
            rows = conn.execute(
                "SELECT * FROM definitions ORDER BY term"
            ).fetchall()
        return [dict(r) for r in rows]

    def append_events(
        self, symbol: str, timeframe: str, events: list[dict[str, Any]]
    ) -> None:
        with _connect(self.path) as conn:
            for ev in events:
                try:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO events
                        (symbol, timeframe, ts, kind, name, ticket, seq)
                        VALUES (?,?,?,?,?,?,?)
                        """,
                        (
                            symbol,
                            timeframe,
                            ev.get("ts"),
                            ev.get("kind"),
                            ev.get("name"),
                            ev.get("ticket"),
                            ev.get("seq"),
                        ),
                    )
                except sqlite3.Error:
                    continue
            conn.commit()
