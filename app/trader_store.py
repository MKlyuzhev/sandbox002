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
CREATE TABLE IF NOT EXISTS order_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    sl REAL,
    tp REAL,
    source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket INTEGER,
    symbol TEXT NOT NULL,
    timeframe TEXT,
    kind TEXT NOT NULL,
    author TEXT NOT NULL,
    model TEXT,
    created_ts INTEGER NOT NULL,
    body_md TEXT NOT NULL,
    refs_json TEXT,
    levels_json TEXT,
    drawn_prefix TEXT,
    attach_reason TEXT
);
"""

ANALYSIS_KINDS = ("pre_trade", "in_trade", "post_trade", "chart_read")
ANALYSIS_AUTHORS = ("claude", "user")
# A pre-trade analysis written up to this long before a ticket opens is
# attached to it automatically (same symbol, not already attached).
ANALYSIS_ATTACH_WINDOW_SEC = 24 * 3600

_LINK_EXTRAS = (("object_json", "TEXT"), ("source", "TEXT"), ("linked_ts", "INTEGER"))
_ORDER_EXTRAS = (("utc_offset", "INTEGER"),)
# A plan is a pre_trade analysis with an order spec (``order_json``) for the
# trader to enter by hand; nothing here sends it.
_ANALYSIS_EXTRAS = (("order_json", "TEXT"), ("status", "TEXT"), ("supersedes", "INTEGER"))
PLAN_STATUSES = ("active", "superseded", "cancelled")
ORDER_TYPES = ("buy_limit", "sell_limit", "buy_stop", "sell_stop")
# Ticket open price vs plan price: a pending order sits on the plan price; a
# market ticket (the pending one filled before the first sync) may slip.
PENDING_MATCH_PIPS = 1.0
FILLED_MATCH_PIPS = 5.0


def _same(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) < 1e-9


def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    for table, extras in (
        ("links", _LINK_EXTRAS), ("orders", _ORDER_EXTRAS), ("analyses", _ANALYSIS_EXTRAS)
    ):
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, typ in extras:
            if name not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")


def _analysis_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["refs"] = json.loads(data.pop("refs_json") or "[]")
    data["levels"] = json.loads(data.pop("levels_json") or "[]")
    data["order"] = json.loads(data.pop("order_json") or "null")
    return data


def _pip(symbol: str) -> float:
    return 0.01 if "JPY" in symbol.upper() else 0.0001


def order_matches(plan_order: dict[str, Any], ticket: dict[str, Any], symbol: str) -> bool:
    """A ticket is this plan's order: same side, and the plan's pending type at
    the plan price, or a market ticket of that side filled near it."""
    ptype = str(plan_order.get("type") or "")
    ttype = str(ticket.get("type") or "")
    side = ptype.split("_")[0]
    try:
        gap = abs(float(ticket.get("open_price")) - float(plan_order["price"])) / _pip(symbol)
    except (TypeError, ValueError, KeyError):
        return False
    if ttype == ptype:
        return gap <= PENDING_MATCH_PIPS
    return ttype == side and gap <= FILLED_MATCH_PIPS


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

    def upsert_order(
        self, order: dict[str, Any], *, status: str, utc_offset: int | None = None
    ) -> bool:
        """Insert or update a ticket. Returns True when the ticket is new.

        ``sl_open``/``tp_open`` and ``utc_offset`` (broker − UTC seconds) are
        kept from the first sighting.
        """
        ticket = int(order["ticket"])
        with _connect(self.path) as conn:
            existing = conn.execute(
                "SELECT sl_open, tp_open, utc_offset FROM orders WHERE ticket=?",
                (ticket,),
            ).fetchone()
            sl_open = existing["sl_open"] if existing else order.get("sl")
            tp_open = existing["tp_open"] if existing else order.get("tp")
            if existing and existing["utc_offset"] is not None:
                utc_offset = existing["utc_offset"]
            conn.execute(
                """
                INSERT INTO orders (
                    ticket, symbol, type, lots, open_price, open_time,
                    sl, tp, magic, comment, close_price, close_time,
                    profit, swap, commission, sl_open, tp_open, status, utc_offset
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(ticket) DO UPDATE SET
                    sl=excluded.sl, tp=excluded.tp, lots=excluded.lots,
                    close_price=excluded.close_price, close_time=excluded.close_time,
                    profit=excluded.profit, swap=excluded.swap,
                    commission=excluded.commission, status=excluded.status,
                    utc_offset=excluded.utc_offset
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
                    utc_offset,
                ),
            )
            conn.commit()
        return existing is None

    def set_open_levels(self, ticket: int, sl_open: float | None, tp_open: float | None) -> None:
        """Correct the planned SL/TP (e.g. seeded from a known first value)."""
        with _connect(self.path) as conn:
            conn.execute(
                "UPDATE orders SET sl_open=?, tp_open=? WHERE ticket=?",
                (sl_open, tp_open, ticket),
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
        """Replace heuristic (``auto``) links. Manual links are never touched."""
        now = _now_ts()
        with _connect(self.path) as conn:
            conn.execute(
                "DELETE FROM links WHERE ticket=? AND COALESCE(source,'auto')='auto'",
                (ticket,),
            )
            for link in links:
                obj = link.get("object")
                conn.execute(
                    """
                    INSERT INTO links
                    (ticket, object_name, role, reason, object_json, source, linked_ts)
                    VALUES (?,?,?,?,?,'auto',?)
                    """,
                    (
                        ticket,
                        link.get("object_name"),
                        link.get("role"),
                        link.get("reason"),
                        json.dumps(obj, default=str) if obj else None,
                        now,
                    ),
                )
            conn.commit()

    def set_manual_links(self, ticket: int, objects: list[dict[str, Any]]) -> None:
        """Link exactly these objects (frozen geometry) to a ticket.

        Replaces every link for the ticket, auto and manual, and drops the
        review so the next sync re-scores against the linked set.
        """
        now = _now_ts()
        with _connect(self.path) as conn:
            conn.execute("DELETE FROM links WHERE ticket=?", (ticket,))
            for obj in objects:
                conn.execute(
                    """
                    INSERT INTO links
                    (ticket, object_name, role, reason, object_json, source, linked_ts)
                    VALUES (?,?,?,'selected',?, 'manual', ?)
                    """,
                    (
                        ticket,
                        obj.get("name"),
                        obj.get("role") or "unknown",
                        json.dumps(obj, default=str),
                        now,
                    ),
                )
            conn.execute(
                "DELETE FROM reviews WHERE episode_id IN "
                "(SELECT id FROM episodes WHERE ticket=?)",
                (ticket,),
            )
            conn.commit()

    def links_for(self, ticket: int) -> list[dict[str, Any]]:
        with _connect(self.path) as conn:
            rows = conn.execute(
                "SELECT * FROM links WHERE ticket=? ORDER BY id", (ticket,)
            ).fetchall()
        out = []
        for row in rows:
            data = dict(row)
            raw = data.pop("object_json", None)
            data["object"] = json.loads(raw) if raw else None
            data["source"] = data.get("source") or "auto"
            out.append(data)
        return out

    def manual_links_for(self, ticket: int) -> list[dict[str, Any]]:
        return [link for link in self.links_for(ticket) if link["source"] == "manual"]

    def add_revision(
        self, ticket: int, ts: int, sl: float | None, tp: float | None, source: str
    ) -> bool:
        """Append an SL/TP revision (``ts`` in broker time).

        Skipped when the revision in force at ``ts`` already has these values,
        or when the identical row exists (events.jsonl is re-read every sync).
        """
        with _connect(self.path) as conn:
            prev = conn.execute(
                """
                SELECT sl, tp FROM order_revisions
                WHERE ticket=? AND ts<=? ORDER BY ts DESC, id DESC LIMIT 1
                """,
                (ticket, ts),
            ).fetchone()
            if prev and _same(prev["sl"], sl) and _same(prev["tp"], tp):
                return False
            dup = conn.execute(
                "SELECT 1 FROM order_revisions WHERE ticket=? AND ts=? AND sl IS ? AND tp IS ?",
                (ticket, ts, sl, tp),
            ).fetchone()
            if dup:
                return False
            conn.execute(
                "INSERT INTO order_revisions (ticket, ts, sl, tp, source) VALUES (?,?,?,?,?)",
                (ticket, ts, sl, tp, source),
            )
            conn.commit()
        return True

    def revisions_for(self, ticket: int) -> list[dict[str, Any]]:
        with _connect(self.path) as conn:
            rows = conn.execute(
                "SELECT * FROM order_revisions WHERE ticket=? ORDER BY ts, id",
                (ticket,),
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
                    timeframe=excluded.timeframe,
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

    def delete_review(self, episode_id: str) -> None:
        with _connect(self.path) as conn:
            conn.execute("DELETE FROM reviews WHERE episode_id=?", (episode_id,))
            conn.commit()

    def add_analysis(
        self,
        *,
        symbol: str,
        kind: str,
        body_md: str,
        author: str = "claude",
        timeframe: str = "",
        ticket: int | None = None,
        model: str = "",
        refs: list[str] | None = None,
        levels: list[dict[str, Any]] | None = None,
        drawn_prefix: str = "",
        created_ts: int | None = None,
        order: dict[str, Any] | None = None,
        supersedes: int | None = None,
    ) -> dict[str, Any]:
        """Store an analysis verbatim. It never changes code-owned review scores.

        ``order`` makes it a plan (validated by ``trader_journal``); ``supersedes``
        marks an earlier plan superseded.
        """
        if kind not in ANALYSIS_KINDS:
            raise ValueError(f"kind must be one of {ANALYSIS_KINDS}")
        if author not in ANALYSIS_AUTHORS:
            raise ValueError(f"author must be one of {ANALYSIS_AUTHORS}")
        if not (body_md or "").strip():
            raise ValueError("body_md is empty")
        with _connect(self.path) as conn:
            cur = conn.execute(
                """
                INSERT INTO analyses (
                    ticket, symbol, timeframe, kind, author, model, created_ts,
                    body_md, refs_json, levels_json, drawn_prefix, attach_reason,
                    order_json, status, supersedes
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    ticket,
                    symbol,
                    timeframe,
                    kind,
                    author,
                    model or None,
                    created_ts or _now_ts(),
                    body_md,
                    json.dumps(refs or []),
                    json.dumps(levels or [], default=str),
                    drawn_prefix or None,
                    "explicit" if ticket else None,
                    json.dumps(order) if order else None,
                    "active" if order else None,
                    supersedes,
                ),
            )
            if supersedes:
                conn.execute(
                    "UPDATE analyses SET status='superseded' WHERE id=?", (int(supersedes),)
                )
            conn.commit()
            analysis_id = int(cur.lastrowid)
        found = self.get_analysis(analysis_id)
        assert found is not None
        return found

    def get_analysis(self, analysis_id: int) -> dict[str, Any] | None:
        with _connect(self.path) as conn:
            row = conn.execute(
                "SELECT * FROM analyses WHERE id=?", (analysis_id,)
            ).fetchone()
        return _analysis_row(row) if row else None

    def attach_analysis(self, analysis_id: int, ticket: int, reason: str = "explicit") -> None:
        with _connect(self.path) as conn:
            conn.execute(
                "UPDATE analyses SET ticket=?, attach_reason=? WHERE id=?",
                (ticket, reason, analysis_id),
            )
            conn.commit()

    def attach_recent_analyses(self, ticket: int, symbol: str, open_ts: int) -> list[int]:
        """Attach unattached pre-trade analyses written shortly before a new ticket.

        ``open_ts`` must be on the same clock as ``created_ts`` (UTC). An active
        plan whose order matches the ticket attaches as ``order_match`` (any age);
        if one does, only it is attached. Otherwise plain analyses (no order) in
        the window attach as ``auto_window``. Superseded or cancelled plans and
        plans whose order does not match are never auto-attached.
        """
        order = self.get_order(ticket) or {}
        with _connect(self.path) as conn:
            plans = conn.execute(
                """
                SELECT id, order_json FROM analyses
                WHERE ticket IS NULL AND symbol=? AND kind='pre_trade'
                  AND order_json IS NOT NULL AND status='active'
                ORDER BY created_ts DESC, id DESC
                """,
                (symbol,),
            ).fetchall()
            matched = [
                int(r["id"]) for r in plans
                if order and order_matches(json.loads(r["order_json"]), order, symbol)
            ][:1]
            if matched:
                ids, reason = matched, "order_match"
            else:
                rows = conn.execute(
                    """
                    SELECT id FROM analyses
                    WHERE ticket IS NULL AND symbol=? AND kind='pre_trade'
                      AND order_json IS NULL AND created_ts BETWEEN ? AND ?
                      AND COALESCE(status, '') NOT IN ('superseded', 'cancelled')
                    """,
                    (symbol, open_ts - ANALYSIS_ATTACH_WINDOW_SEC, open_ts),
                ).fetchall()
                ids, reason = [int(r["id"]) for r in rows], "auto_window"
            for aid in ids:
                conn.execute(
                    "UPDATE analyses SET ticket=?, attach_reason=? WHERE id=?",
                    (ticket, reason, aid),
                )
            conn.commit()
        return ids

    def set_plan_status(self, analysis_id: int, status: str) -> None:
        if status not in PLAN_STATUSES:
            raise ValueError(f"status must be one of {PLAN_STATUSES}")
        with _connect(self.path) as conn:
            cur = conn.execute(
                "UPDATE analyses SET status=? WHERE id=? AND order_json IS NOT NULL",
                (status, analysis_id),
            )
            conn.commit()
        if cur.rowcount == 0:
            raise ValueError(f"analysis {analysis_id} is not a plan")

    def set_plan_order(
        self, analysis_id: int, order: dict[str, Any], supersedes: int | None = None
    ) -> None:
        """Give an existing analysis its order spec (backfill); validated by the caller."""
        with _connect(self.path) as conn:
            conn.execute(
                "UPDATE analyses SET order_json=?, status=COALESCE(status, 'active'), "
                "supersedes=COALESCE(?, supersedes) WHERE id=?",
                (json.dumps(order), supersedes, analysis_id),
            )
            if supersedes:
                conn.execute(
                    "UPDATE analyses SET status='superseded' WHERE id=?", (int(supersedes),)
                )
            conn.commit()

    def list_plans(self, symbol: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        sql = "SELECT * FROM analyses WHERE order_json IS NOT NULL"
        args: list[Any] = []
        if symbol:
            sql += " AND symbol=?"
            args.append(symbol)
        sql += " ORDER BY created_ts DESC, id DESC LIMIT ?"
        args.append(max(1, min(int(limit), 500)))
        with _connect(self.path) as conn:
            rows = conn.execute(sql, args).fetchall()
        return [_analysis_row(r) for r in rows]

    def list_analyses(
        self,
        *,
        ticket: int | None = None,
        symbol: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM analyses"
        clauses: list[str] = []
        args: list[Any] = []
        if ticket is not None:
            clauses.append("ticket=?")
            args.append(ticket)
        if symbol:
            clauses.append("symbol=?")
            args.append(symbol)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_ts, id LIMIT ?"
        args.append(max(1, min(int(limit), 500)))
        with _connect(self.path) as conn:
            rows = conn.execute(sql, args).fetchall()
        return [_analysis_row(r) for r in rows]

    def get_record(self, ticket: int) -> dict[str, Any] | None:
        """Everything journaled for one ticket."""
        order = self.get_order(ticket)
        if order is None:
            return None
        with _connect(self.path) as conn:
            ep_row = conn.execute(
                "SELECT * FROM episodes WHERE ticket=? ORDER BY rowid DESC LIMIT 1",
                (ticket,),
            ).fetchone()
        episode = dict(ep_row) if ep_row else None
        review = self.get_review(episode["id"]) if episode else None
        if episode is not None:
            episode.pop("objects_json", None)
        return {
            "order": order,
            "revisions": self.revisions_for(ticket),
            "links": self.links_for(ticket),
            "episode": episode,
            "review": review,
            "analyses": self.list_analyses(ticket=ticket, limit=500),
        }
