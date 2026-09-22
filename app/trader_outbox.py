"""Parse MT4 trader outbox files (chart objects, orders, events)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app import mt4_bridge

IGNORE_NAME_PREFIXES = ("sbox.", "TA_PANEL_", "TARGET_LINE_")
ROLE_RE = re.compile(r"role\s*=\s*([a-zA-Z0-9_-]+)", re.I)
TF_RE = re.compile(r"tf\s*=\s*([A-Za-z0-9]+)", re.I)
NOTE_RE = re.compile(r"note\s*=\s*([^\n]+)", re.I)

OANDA_TF = {
    "M1": "M1",
    "M5": "M5",
    "M15": "M15",
    "M30": "M30",
    "H1": "H1",
    "H4": "H4",
    "D1": "D",
    "D": "D",
    "W1": "W",
    "W": "W",
    "MN1": "MN",
}


def parse_role_tags(text: str = "", tooltip: str = "") -> dict[str, str]:
    blob = f"{text or ''} {tooltip or ''}".strip()
    role_m = ROLE_RE.search(blob)
    tf_m = TF_RE.search(blob)
    note_m = NOTE_RE.search(blob)
    role = role_m.group(1).lower() if role_m else "unknown"
    return {
        "role": role,
        "tf": tf_m.group(1) if tf_m else "",
        "note": note_m.group(1).strip() if note_m else "",
        "blob": blob,
    }


def is_ignored_name(name: str) -> bool:
    n = name or ""
    return any(n.startswith(prefix) for prefix in IGNORE_NAME_PREFIXES)


def annotate_object(obj: dict[str, Any]) -> dict[str, Any]:
    out = dict(obj)
    tags = parse_role_tags(str(obj.get("text") or ""), str(obj.get("tooltip") or ""))
    out["role"] = tags["role"]
    out["role_tf"] = tags["tf"]
    out["role_note"] = tags["note"]
    return out


def filter_user_objects(objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for obj in objects:
        name = str(obj.get("name") or "")
        if is_ignored_name(name):
            continue
        kept.append(annotate_object(obj))
    return kept


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    except OSError:
        return []
    return rows


def load_chart(chart_dir: Path) -> dict[str, Any]:
    payload = _load_json(chart_dir / "chart.json")
    objects = filter_user_objects(list(payload.get("objects") or []))
    payload["objects"] = objects
    return payload


def load_orders(chart_dir: Path, *, history: bool = False) -> dict[str, Any]:
    name = "history.json" if history else "orders.json"
    return _load_json(chart_dir / name)


def oanda_granularity(timeframe: str) -> str:
    key = (timeframe or "").strip().upper()
    return OANDA_TF.get(key, key)


def oanda_instrument(symbol: str) -> str:
    """GBPUSD → GBP_USD when possible; otherwise pass through."""
    raw = (symbol or "").replace("/", "").strip().upper()
    if "_" in raw:
        return raw
    if len(raw) == 6:
        return f"{raw[:3]}_{raw[3:]}"
    return mt4_bridge.map_symbol(symbol)


def iter_chart_dirs(files_dir: Path | None = None) -> list[Path]:
    root = mt4_bridge.inbox_dir() if files_dir is None else Path(files_dir) / mt4_bridge.INBOX_SUBDIR
    if not root.is_dir():
        return []
    out: list[Path] = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "heartbeat.json").is_file():
            out.append(child)
    return out
