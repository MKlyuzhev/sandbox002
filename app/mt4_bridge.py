"""Wine-safe MT4 chart-object bridge (file inbox, no orders).

Python writes ``cmd.json`` under ``MQL4/Files/sandbox002/``; the
``SandboxChartBridge`` EA polls it and draws ``OBJ_*`` objects. Heartbeat
from the EA supplies chart symbol/period and ``TimeCurrent`` vs ``TimeGMT``
so OANDA RFC3339 timestamps can be shifted to broker time.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger("mt4_bridge")

INBOX_SUBDIR = "sandbox002"
CMD_NAME = "cmd.json"
HEARTBEAT_NAME = "heartbeat.json"
DEFAULT_PREFIX = "sbox."
FORMATION_PREFIX = "sbox.formation."
REGIME_PREFIX = "sbox.regime."
REGIME_WALK_PREFIX = "sbox.regime.walk."
TICKET_PREFIX = "sbox.ticket."
TICKET_WALK_PREFIX = "sbox.ticket.walk."
WALK_SHOW_RANGES = "ranges"
WALK_SHOW_MARKERS = "markers"
WALK_SHOW_BOTH = "both"
WALK_SHOW_CHOICES = (WALK_SHOW_RANGES, WALK_SHOW_MARKERS, WALK_SHOW_BOTH)
HEARTBEAT_STALE_SEC = 10.0
CMD_ACK_TIMEOUT_SEC = 8.0
CMD_ACK_POLL_SEC = 0.05
# Unit tests set this False so a static fake heartbeat does not block.
WAIT_FOR_ACK = True
REGIME_LOOKBACK = 80
REGIME_STRIDE = 2
REGIME_MAX_OBJECTS = 400

# Color/style must match the polylines in regime_to_objects (price pane only).
REGIME_LEGEND = (
    ("sma10", "white", "SMA 10"),
    ("sma20", "orange", "SMA 20"),
    ("sma50", "red", "SMA 50"),
    ("sma100", "blue", "SMA 100 dash"),
    ("sma200", "purple", "SMA 200 dash"),
    ("bb2", "grey", "BB 2sd"),
    ("bb1", "cyan", "BB 1sd dash"),
    ("high_n", "green", "10-bar high dot"),
    ("low_n", "orange", "10-bar low dot"),
)
REGIME_LABEL_X = 8
REGIME_LABEL_Y = 18
REGIME_LEGEND_Y0 = 36
REGIME_LEGEND_DY = 14
TICKET_LABEL_X = 8
TICKET_LABEL_Y = 170

OBJECT_TYPES = frozenset(
    {"trend", "hline", "vline", "text", "arrow", "rectangle", "label"}
)
STYLE_NAMES = frozenset({"solid", "dash", "dot", "dashdot"})

GRANULARITY_TO_PERIOD: dict[str, tuple[str, int]] = {
    "M1": ("M1", 1),
    "M5": ("M5", 5),
    "M15": ("M15", 15),
    "M30": ("M30", 30),
    "H1": ("H1", 60),
    "H4": ("H4", 240),
    "D": ("D1", 1440),
    "D1": ("D1", 1440),
    "W": ("W1", 10080),
    "W1": ("W1", 10080),
    "MN": ("MN1", 43200),
    "MN1": ("MN1", 43200),
}

PERIOD_TO_TF = {v[1]: v[0] for v in GRANULARITY_TO_PERIOD.values()}


class Mt4BridgeError(RuntimeError):
    """Raised when the MT4 inbox/chart cannot accept a command."""


def files_dir() -> Path:
    return Path(settings.mt4_files_dir).expanduser()


def inbox_dir() -> Path:
    return files_dir() / INBOX_SUBDIR


def ensure_inbox() -> Path:
    path = inbox_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def map_symbol(instrument: str) -> str:
    """OANDA ``GBP_USD`` → MT4 ``GBPUSD``."""
    return instrument.replace("_", "").replace("/", "").strip().upper()


def map_timeframe(granularity: str) -> tuple[str, int]:
    key = granularity.strip().upper()
    if key not in GRANULARITY_TO_PERIOD:
        raise Mt4BridgeError(
            f"Unsupported timeframe '{granularity}'; "
            f"expected one of {sorted(GRANULARITY_TO_PERIOD)}."
        )
    return GRANULARITY_TO_PERIOD[key]


def parse_rfc3339_utc(value: str | None) -> int | None:
    """Parse OANDA RFC3339 (possibly nanosecond) to UTC unix seconds."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "." in text:
        head, rest = text.split(".", 1)
        tz_sep = "+" if "+" in rest else ("-" if "-" in rest[1:] else "")
        if tz_sep:
            idx = rest.find(tz_sep, 1) if tz_sep == "-" else rest.find(tz_sep)
            frac, tz = rest[:idx], rest[idx:]
        else:
            frac, tz = rest, "+00:00"
        frac = "".join(c for c in frac if c.isdigit())[:6].ljust(6, "0")
        text = f"{head}.{frac}{tz}"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def broker_time(utc_unix: int, offset_seconds: int) -> int:
    return int(utc_unix) + int(offset_seconds)


def offset_from_heartbeat(heartbeat: dict[str, Any] | None) -> int:
    if not heartbeat:
        return 0
    if heartbeat.get("offset_seconds") is not None:
        return int(heartbeat["offset_seconds"])
    tc = heartbeat.get("time_current")
    tg = heartbeat.get("time_gmt")
    if tc is None or tg is None:
        return 0
    return int(tc) - int(tg)


def read_heartbeat() -> dict[str, Any] | None:
    path = inbox_dir() / HEARTBEAT_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def heartbeat_age_sec(path: Path | None = None) -> float | None:
    hb_path = path or (inbox_dir() / HEARTBEAT_NAME)
    if not hb_path.is_file():
        return None
    return max(0.0, time.time() - hb_path.stat().st_mtime)


def status() -> dict[str, Any]:
    inbox = inbox_dir()
    writable = False
    try:
        ensure_inbox()
        probe = inbox / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        writable = True
    except OSError:
        writable = False
    hb = read_heartbeat()
    age = heartbeat_age_sec()
    ea_ok = bool(
        hb
        and hb.get("ea_ok", True)
        and age is not None
        and age <= HEARTBEAT_STALE_SEC
    )
    period = hb.get("period") if hb else None
    timeframe = None
    if hb:
        timeframe = hb.get("timeframe") or PERIOD_TO_TF.get(int(period or 0))
    return {
        "inbox_dir": str(inbox),
        "inbox_writable": writable,
        "heartbeat": hb,
        "heartbeat_age_sec": age,
        "ea_ok": ea_ok,
        "symbol": (hb or {}).get("symbol"),
        "period": period,
        "timeframe": timeframe,
        "last_cmd_id": (hb or {}).get("last_cmd_id"),
        "last_error": (hb or {}).get("last_error") or "",
        "object_count": (hb or {}).get("object_count"),
        "last_prefix": (hb or {}).get("last_prefix"),
    }


def _atomic_write_json(
    path: Path, payload: dict[str, Any], *, indent: int | None = None
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    if indent is None:
        text = json.dumps(payload, separators=(",", ":"))
    else:
        text = json.dumps(payload, indent=indent)
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def wait_for_cmd_ack(cmd_id: str, timeout: float = CMD_ACK_TIMEOUT_SEC) -> bool:
    """Block until the EA heartbeat reports ``last_cmd_id``.

    Returns True if acked, if there is no live EA, or if waiting is disabled.
    The inbox is a single ``cmd.json``; callers must wait before the next write
    or the EA will only see the last command (regime overlay lost to ticket).
    """
    if not WAIT_FOR_ACK or not cmd_id:
        return True
    st = status()
    if not st.get("ea_ok"):
        return True
    deadline = time.monotonic() + max(0.05, float(timeout))
    while time.monotonic() < deadline:
        hb = read_heartbeat() or {}
        if hb.get("last_cmd_id") == cmd_id:
            return True
        time.sleep(CMD_ACK_POLL_SEC)
    logger.warning(
        "MT4 EA did not ack cmd %s within %.1fs (last_cmd_id=%r)",
        cmd_id,
        timeout,
        (read_heartbeat() or {}).get("last_cmd_id"),
    )
    return False


def write_command(payload: dict[str, Any]) -> str:
    cmd_id = payload.get("id") or str(uuid.uuid4())
    payload = dict(payload)
    payload["id"] = cmd_id
    # Pretty JSON: MQL4 FILE_TXT FileReadString stops at '\\n' or 4095 chars.
    # A compact one-line cmd.json was split mid-number (t1), so SMA/BB trends
    # landed in 1970 and only corner labels (no t1) stayed visible.
    _atomic_write_json(ensure_inbox() / CMD_NAME, payload, indent=2)
    wait_for_cmd_ack(cmd_id)
    return cmd_id


def _normalize_object(obj: dict[str, Any], prefix: str) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise Mt4BridgeError("each object must be a dict")
    otype = str(obj.get("type") or "").strip().lower()
    if otype not in OBJECT_TYPES:
        raise Mt4BridgeError(
            f"Unsupported object type '{obj.get('type')}'; "
            f"expected one of {sorted(OBJECT_TYPES)}."
        )
    name = str(obj.get("name") or "").strip()
    if not name:
        raise Mt4BridgeError("object is missing 'name'")
    if prefix and not name.startswith(prefix):
        name = f"{prefix}{name}"
    style = str(obj.get("style") or "solid").strip().lower()
    if style not in STYLE_NAMES:
        raise Mt4BridgeError(f"Unsupported style '{style}'")
    out: dict[str, Any] = {
        "name": name,
        "type": otype,
        "color": str(obj.get("color") or "white"),
        "style": style,
        "width": int(obj.get("width") or 1),
        "ray": bool(obj.get("ray", False)),
        "window": int(obj.get("window") or 0),
    }
    for key in ("t1", "t2", "x", "y", "arrow_code"):
        if obj.get(key) is not None:
            out[key] = int(obj[key])
    for key in ("p1", "p2"):
        if obj.get(key) is not None:
            out[key] = float(obj[key])
    if obj.get("text") is not None:
        out["text"] = str(obj["text"])
    if otype in {"trend", "rectangle"} and ("t1" not in out or "p1" not in out):
        raise Mt4BridgeError(f"{otype} requires t1 and p1")
    if otype == "hline" and "p1" not in out:
        raise Mt4BridgeError("hline requires p1")
    if otype == "vline" and "t1" not in out:
        raise Mt4BridgeError("vline requires t1")
    if otype in {"text", "arrow"} and ("t1" not in out or "p1" not in out):
        raise Mt4BridgeError(f"{otype} requires t1 and p1")
    if otype == "label" and ("x" not in out or "y" not in out):
        raise Mt4BridgeError("label requires x and y")
    return out


def check_chart(
    symbol: str,
    timeframe: str,
    heartbeat: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    st = status() if heartbeat is None else None
    hb = heartbeat if heartbeat is not None else (st or {}).get("heartbeat")
    ea_ok = True if heartbeat is not None else bool((st or {}).get("ea_ok"))
    if heartbeat is None and not ea_ok:
        return False, "EA heartbeat missing or stale; attach SandboxChartBridge."
    if not hb:
        return False, "EA heartbeat missing; attach SandboxChartBridge."
    want_sym = map_symbol(symbol)
    want_tf, want_period = map_timeframe(timeframe)
    got_sym = str(hb.get("symbol") or "").replace("_", "").upper()
    got_period = int(hb.get("period") or 0)
    got_tf = str(hb.get("timeframe") or PERIOD_TO_TF.get(got_period) or "")
    if got_sym != want_sym:
        return False, f"Chart symbol {got_sym or '?'} != {want_sym}."
    if got_period and got_period != want_period:
        return False, f"Chart period {got_period} ({got_tf}) != {want_tf} ({want_period})."
    if not got_period and got_tf and got_tf.upper() != want_tf:
        return False, f"Chart timeframe {got_tf} != {want_tf}."
    return True, ""


def upsert_objects(
    symbol: str,
    timeframe: str,
    objects: list[dict[str, Any]],
    prefix: str = DEFAULT_PREFIX,
    clear_prefix_first: bool = True,
    require_chart_match: bool = True,
) -> dict[str, Any]:
    st = status()
    if require_chart_match:
        ok, reason = check_chart(symbol, timeframe, heartbeat=st.get("heartbeat"))
        if not st.get("ea_ok") or not ok:
            return {
                "ok": False,
                "error": (
                    "EA heartbeat missing or stale; attach SandboxChartBridge."
                    if not st.get("ea_ok")
                    else reason
                ),
                "chart_ok": False,
                "status": st,
            }
    tf_name, _period = map_timeframe(timeframe)
    normalized = [_normalize_object(o, prefix) for o in objects]
    cmd_id = write_command(
        {
            "op": "upsert",
            "symbol": map_symbol(symbol),
            "timeframe": tf_name,
            "prefix": prefix,
            "clear_prefix_first": clear_prefix_first,
            "objects": normalized,
        }
    )
    return {
        "ok": True,
        "cmd_id": cmd_id,
        "objects_written": len(normalized),
        "chart_ok": True if require_chart_match else None,
        "prefix": prefix,
        "status": st,
    }


def delete_objects(
    names: list[str] | None = None,
    prefix: str = "",
) -> dict[str, Any]:
    st = status()
    if not st.get("ea_ok"):
        return {
            "ok": False,
            "error": "EA heartbeat missing or stale; attach SandboxChartBridge.",
            "chart_ok": False,
            "status": st,
        }
    cmd_id = write_command(
        {
            "op": "delete",
            "names": list(names or []),
            "prefix": prefix,
        }
    )
    return {"ok": True, "cmd_id": cmd_id, "status": st}


def clear_layer(prefix: str = DEFAULT_PREFIX) -> dict[str, Any]:
    st = status()
    if not st.get("ea_ok"):
        return {
            "ok": False,
            "error": "EA heartbeat missing or stale; attach SandboxChartBridge.",
            "chart_ok": False,
            "status": st,
        }
    cmd_id = write_command({"op": "clear", "prefix": prefix})
    return {"ok": True, "cmd_id": cmd_id, "prefix": prefix, "status": st}


def _bar_broker_time(
    bars: list[dict[str, Any]], index: int, offset_seconds: int
) -> int | None:
    if index < 0 or index >= len(bars):
        return None
    utc = parse_rfc3339_utc(bars[index].get("time"))
    if utc is None:
        return None
    return broker_time(utc, offset_seconds)


def formation_to_objects(
    analysis: dict[str, Any],
    bars: list[dict[str, Any]],
    offset_seconds: int = 0,
    prefix: str = FORMATION_PREFIX,
) -> list[dict[str, Any]]:
    """Map ``patterns.analyze_bars`` + bars to MT4 object dicts (plot layers)."""
    objects: list[dict[str, Any]] = []
    last_i = len(bars) - 1
    last_t = _bar_broker_time(bars, last_i, offset_seconds)

    for n, swing in enumerate(analysis.get("swings") or []):
        idx = swing.get("index")
        price = swing.get("price")
        if idx is None or price is None:
            continue
        t1 = _bar_broker_time(bars, int(idx), offset_seconds)
        if t1 is None:
            continue
        high = swing.get("kind") == "high"
        objects.append(
            {
                "name": f"{prefix}swing.{n}",
                "type": "arrow",
                "t1": t1,
                "p1": float(price),
                "color": "blue" if high else "orange",
                "arrow_code": 234 if high else 233,
                "width": 1,
            }
        )

    for n, line in enumerate(analysis.get("trendlines") or []):
        i0, i1 = line.get("i0"), line.get("i1")
        p0, p1 = line.get("price0"), line.get("price1")
        if None in (i0, i1, p0, p1):
            continue
        t0 = _bar_broker_time(bars, int(i0), offset_seconds)
        t1 = _bar_broker_time(bars, int(i1), offset_seconds)
        if t0 is None or t1 is None:
            continue
        color = "green" if line.get("kind") == "support" else "red"
        objects.append(
            {
                "name": f"{prefix}trend.{n}",
                "type": "trend",
                "t1": t0,
                "p1": float(p0),
                "t2": t1,
                "p2": float(p1),
                "color": color,
                "style": "solid",
                "width": 1,
                "ray": False,
            }
        )
        at_last = line.get("price_at_last")
        if at_last is not None and last_t is not None and int(i1) < last_i:
            objects.append(
                {
                    "name": f"{prefix}trend.{n}.ext",
                    "type": "trend",
                    "t1": t1,
                    "p1": float(p1),
                    "t2": last_t,
                    "p2": float(at_last),
                    "color": color,
                    "style": "dash",
                    "width": 1,
                    "ray": False,
                }
            )

    hs = analysis.get("hs") or {}
    for key, label, color in (
        ("left_shoulder", "LS", "purple"),
        ("head", "H", "pink"),
        ("right_shoulder", "RS", "brown"),
    ):
        pt = hs.get(key)
        if not pt:
            continue
        idx, price = pt.get("index"), pt.get("price")
        if idx is None or price is None:
            continue
        t1 = _bar_broker_time(bars, int(idx), offset_seconds)
        if t1 is None:
            continue
        objects.append(
            {
                "name": f"{prefix}hs.{key}.arrow",
                "type": "arrow",
                "t1": t1,
                "p1": float(price),
                "color": color,
                "arrow_code": 159,
                "width": 1,
            }
        )
        objects.append(
            {
                "name": f"{prefix}hs.{key}.text",
                "type": "text",
                "t1": t1,
                "p1": float(price),
                "color": color,
                "text": label,
                "width": 1,
            }
        )

    nl = hs.get("neckline")
    if nl:
        i0, i1 = nl.get("i0"), nl.get("i1")
        p0, p1 = nl.get("price0"), nl.get("price1")
        if None not in (i0, i1, p0, p1):
            t0 = _bar_broker_time(bars, int(i0), offset_seconds)
            t1 = _bar_broker_time(bars, int(i1), offset_seconds)
            if t0 is not None and t1 is not None:
                objects.append(
                    {
                        "name": f"{prefix}neckline",
                        "type": "trend",
                        "t1": t0,
                        "p1": float(p0),
                        "t2": t1,
                        "p2": float(p1),
                        "color": "cyan",
                        "style": "solid",
                        "width": 2,
                        "ray": False,
                    }
                )
                at_last = nl.get("price_at_last")
                if at_last is not None and last_t is not None and int(i1) < last_i:
                    objects.append(
                        {
                            "name": f"{prefix}neckline.ext",
                            "type": "trend",
                            "t1": t1,
                            "p1": float(p1),
                            "t2": last_t,
                            "p2": float(at_last),
                            "color": "cyan",
                            "style": "dash",
                            "width": 1,
                            "ray": False,
                        }
                    )

    if hs.get("stage") == "confirmed_break" and hs.get("min_target") is not None:
        objects.append(
            {
                "name": f"{prefix}min_target",
                "type": "hline",
                "p1": float(hs["min_target"]),
                "color": "grey",
                "style": "dot",
                "width": 1,
            }
        )

    stage = hs.get("stage") or "none"
    objects.append(
        {
            "name": f"{prefix}stage",
            "type": "label",
            "x": 8,
            "y": 18,
            "color": "white",
            "text": f"stage={stage}",
        }
    )
    return objects


def apply_formation(
    analysis: dict[str, Any],
    bars: list[dict[str, Any]],
    instrument: str,
    granularity: str,
    prefix: str = FORMATION_PREFIX,
    require_chart_match: bool = True,
) -> dict[str, Any]:
    st = status()
    offset = offset_from_heartbeat(st.get("heartbeat"))
    objects = formation_to_objects(analysis, bars, offset, prefix=prefix)
    result = upsert_objects(
        instrument,
        granularity,
        objects,
        prefix=prefix,
        clear_prefix_first=True,
        require_chart_match=require_chart_match,
    )
    result["analysis"] = analysis
    result["offset_seconds"] = offset
    return result


async def draw_formation(
    instrument: str,
    granularity: str = "H1",
    count: int | None = 200,
    from_time: str | None = None,
    to_time: str | None = None,
    swing_left: int = 3,
    swing_right: int = 3,
    max_lines: int = 5,
    break_frac: float = 0.001,
    prefix: str = FORMATION_PREFIX,
    require_chart_match: bool = True,
) -> dict[str, Any]:
    """Fetch OANDA candles, analyze, and drop a formation overlay command."""
    from app import oanda_client, patterns

    use_count = count
    if from_time and to_time:
        use_count = None
    payload = await oanda_client.get_candles(
        instrument,
        granularity=granularity,
        count=use_count,
        price="M",
        from_time=from_time,
        to_time=to_time,
    )
    bars = oanda_client.candles_to_bars(payload, prefer="mid")
    if len(bars) < 20:
        raise Mt4BridgeError(f"Not enough bars ({len(bars)}); need more history.")
    analysis = patterns.analyze_bars(
        bars,
        swing_left=swing_left,
        swing_right=swing_right,
        max_lines=max_lines,
        break_frac=break_frac,
    )
    analysis["instrument"] = instrument
    analysis["granularity"] = granularity
    if from_time:
        analysis["from_time"] = from_time
    if to_time:
        analysis["to_time"] = to_time
    if use_count is not None:
        analysis["count"] = use_count
    return apply_formation(
        analysis,
        bars,
        instrument,
        granularity,
        prefix=prefix,
        require_chart_match=require_chart_match,
    )


def _polyline_segments(
    bars: list[dict[str, Any]],
    values: list[float | None],
    offset_seconds: int,
    start: int,
    stride: int,
    name_prefix: str,
    color: str,
    style: str = "solid",
    width: int = 1,
) -> list[dict[str, Any]]:
    """Connected trend segments over valid (non-None) values."""
    objects: list[dict[str, Any]] = []
    n = len(bars)
    if stride < 1:
        stride = 1
    i = start
    seg = 0
    while i < n:
        if i >= len(values) or values[i] is None:
            i += 1
            continue
        j = min(i + stride, n - 1)
        while j < n and (j >= len(values) or values[j] is None):
            j += 1
        if j >= n or values[j] is None or j == i:
            break
        t1 = _bar_broker_time(bars, i, offset_seconds)
        t2 = _bar_broker_time(bars, j, offset_seconds)
        p1, p2 = values[i], values[j]
        if t1 is not None and t2 is not None and p1 is not None and p2 is not None:
            objects.append(
                {
                    "name": f"{name_prefix}.{seg}",
                    "type": "trend",
                    "t1": t1,
                    "p1": float(p1),
                    "t2": t2,
                    "p2": float(p2),
                    "color": color,
                    "style": style,
                    "width": width,
                    "ray": False,
                }
            )
            seg += 1
        i = j
    return objects


def _regime_legend_objects(prefix: str) -> list[dict[str, Any]]:
    """Corner labels mapping overlay colors to SMA / Bollinger / 10-bar levels."""
    objects: list[dict[str, Any]] = []
    for i, (key, color, text) in enumerate(REGIME_LEGEND):
        objects.append(
            {
                "name": f"{prefix}legend.{key}",
                "type": "label",
                "x": REGIME_LABEL_X,
                "y": REGIME_LEGEND_Y0 + i * REGIME_LEGEND_DY,
                "color": color,
                "text": text,
            }
        )
    return objects


def regime_to_objects(
    analysis: dict[str, Any],
    bars: list[dict[str, Any]],
    offset_seconds: int = 0,
    prefix: str = REGIME_PREFIX,
    lookback: int = REGIME_LOOKBACK,
    stride: int = REGIME_STRIDE,
) -> list[dict[str, Any]]:
    """Map Lien regime snapshot to MT4 price-pane objects (no oscillator panes)."""
    from app import indicators

    series = indicators.plot_series(bars, lookback=lookback)
    start = int(series["start_index"])
    objects: list[dict[str, Any]] = []

    sma_spec = (
        ("10", "white", "solid", 1),
        ("20", "orange", "solid", 1),
        ("50", "red", "solid", 1),
        ("100", "blue", "dash", 1),
        ("200", "purple", "dash", 1),
    )
    for period, color, style, width in sma_spec:
        objects.extend(
            _polyline_segments(
                bars,
                series["sma"][period],
                offset_seconds,
                start,
                stride,
                f"{prefix}sma{period}",
                color,
                style,
                width,
            )
        )

    bb_spec = (
        ("upper_2", "grey", "solid"),
        ("upper_1", "cyan", "dash"),
        ("lower_1", "cyan", "dash"),
        ("lower_2", "grey", "solid"),
    )
    for key, color, style in bb_spec:
        objects.extend(
            _polyline_segments(
                bars,
                series["bollinger"][key],
                offset_seconds,
                start,
                stride,
                f"{prefix}bb.{key}",
                color,
                style,
                1,
            )
        )

    # If the overlay is too heavy, rebuild with a wider stride.
    overlay_n = len(objects)
    if overlay_n > REGIME_MAX_OBJECTS and stride < 8:
        return regime_to_objects(
            analysis,
            bars,
            offset_seconds=offset_seconds,
            prefix=prefix,
            lookback=lookback,
            stride=stride + 2,
        )

    snap = analysis.get("snapshot") or {}
    hi_n, lo_n = snap.get("high_n"), snap.get("low_n")
    if hi_n is not None:
        objects.append(
            {
                "name": f"{prefix}high_n",
                "type": "hline",
                "p1": float(hi_n),
                "color": "green",
                "style": "dot",
                "width": 1,
            }
        )
    if lo_n is not None:
        objects.append(
            {
                "name": f"{prefix}low_n",
                "type": "hline",
                "p1": float(lo_n),
                "color": "orange",
                "style": "dot",
                "width": 1,
            }
        )

    adx = (snap.get("adx") or {})
    plays = analysis.get("allowed_play_classes") or []
    plays_txt = ",".join(str(p) for p in plays)
    slope = adx.get("slope")
    slope_txt = "rising" if adx.get("rising") else ("falling" if slope is not None else "?")
    label = (
        f"regime={analysis.get('regime', '?')} "
        f"ADX={adx.get('adx', '?')} {slope_txt} "
        f"perfect_order={analysis.get('ma_perfect_order')} "
        f"plays={plays_txt}"
    )
    objects.append(
        {
            "name": f"{prefix}label",
            "type": "label",
            "x": REGIME_LABEL_X,
            "y": REGIME_LABEL_Y,
            "color": "white",
            "text": label,
        }
    )
    objects.extend(_regime_legend_objects(prefix))
    return objects


def apply_regime(
    analysis: dict[str, Any],
    bars: list[dict[str, Any]],
    instrument: str,
    granularity: str,
    prefix: str = REGIME_PREFIX,
    require_chart_match: bool = True,
) -> dict[str, Any]:
    st = status()
    offset = offset_from_heartbeat(st.get("heartbeat"))
    objects = regime_to_objects(analysis, bars, offset, prefix=prefix)
    result = upsert_objects(
        instrument,
        granularity,
        objects,
        prefix=prefix,
        clear_prefix_first=True,
        require_chart_match=require_chart_match,
    )
    result["analysis"] = analysis
    result["offset_seconds"] = offset
    result["objects_drawn"] = len(objects)
    return result


def _format_ticket_ts(at_time: str | int) -> str:
    if isinstance(at_time, str):
        text = at_time.strip()
        if not text:
            return ""
        if text.endswith("Z"):
            text = text[:-1]
        return text.replace("T", " ").split("+")[0][:19] + " UTC"
    dt = datetime.fromtimestamp(int(at_time), tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _ticket_broker_time(
    at_time: str | int | None, offset_seconds: int
) -> tuple[int | None, str]:
    if at_time is None or at_time == "":
        return None, ""
    if isinstance(at_time, str):
        utc = parse_rfc3339_utc(at_time)
        if utc is None:
            return None, ""
        return broker_time(utc, offset_seconds), _format_ticket_ts(at_time)
    utc = int(at_time)
    return broker_time(utc, offset_seconds), _format_ticket_ts(utc)


def ticket_to_objects(
    entry: float,
    stop: float,
    target: float,
    side: str = "none",
    prefix: str = TICKET_PREFIX,
    at_time: int | None = None,
    time_label: str = "",
) -> list[dict[str, Any]]:
    """Horizontal entry / stop / target, optional time marker. Display only."""
    try:
        entry_f, stop_f, target_f = float(entry), float(stop), float(target)
    except (TypeError, ValueError) as exc:
        raise Mt4BridgeError("ticket requires numeric entry, stop, and target") from exc
    if min(entry_f, stop_f, target_f) <= 0:
        raise Mt4BridgeError("ticket prices must be positive")
    if entry_f == stop_f:
        raise Mt4BridgeError("entry and stop must differ")
    side_txt = (side or "none").strip().lower() or "none"
    lines = (
        ("entry", entry_f, "orange", "solid", 2),
        ("stop", stop_f, "red", "dash", 1),
        ("target", target_f, "green", "dash", 1),
    )
    objects: list[dict[str, Any]] = []
    for name, price, color, style, width in lines:
        objects.append(
            {
                "name": f"{prefix}{name}",
                "type": "hline",
                "p1": price,
                "color": color,
                "style": style,
                "width": width,
            }
        )
    stamp = time_label.strip()
    if at_time is not None:
        objects.append(
            {
                "name": f"{prefix}time",
                "type": "vline",
                "t1": int(at_time),
                "color": "orange",
                "style": "dash",
                "width": 1,
            }
        )
        objects.append(
            {
                "name": f"{prefix}time.arrow",
                "type": "arrow",
                "t1": int(at_time),
                "p1": entry_f,
                "color": "orange",
                "arrow_code": 233 if side_txt == "long" else 234,
                "width": 2,
            }
        )
        objects.append(
            {
                "name": f"{prefix}time.text",
                "type": "text",
                "t1": int(at_time),
                "p1": entry_f,
                "color": "orange",
                "text": stamp or "ticket",
            }
        )
    label = (
        f"ticket {side_txt} "
        f"entry={entry_f} stop={stop_f} target={target_f}"
    )
    if stamp:
        label = f"{label} @ {stamp}"
    objects.append(
        {
            "name": f"{prefix}label",
            "type": "label",
            "x": TICKET_LABEL_X,
            "y": TICKET_LABEL_Y,
            "color": "white",
            "text": label,
        }
    )
    return objects


def apply_ticket(
    instrument: str,
    granularity: str,
    entry: float,
    stop: float,
    target: float,
    side: str = "none",
    prefix: str = TICKET_PREFIX,
    require_chart_match: bool = True,
    at_time: str | int | None = None,
) -> dict[str, Any]:
    """Draw a paper/signal ticket on the EA chart. No orders.

    ``at_time`` is UTC unix seconds or RFC3339 (the decision bar). Broker
    offset comes from the EA heartbeat.
    """
    st = status()
    offset = offset_from_heartbeat(st.get("heartbeat"))
    broker_t, time_label = _ticket_broker_time(at_time, offset)
    try:
        objects = ticket_to_objects(
            entry,
            stop,
            target,
            side=side,
            prefix=prefix,
            at_time=broker_t,
            time_label=time_label,
        )
    except Mt4BridgeError as exc:
        return {"ok": False, "error": str(exc), "chart_ok": False}
    result = upsert_objects(
        instrument,
        granularity,
        objects,
        prefix=prefix,
        clear_prefix_first=True,
        require_chart_match=require_chart_match,
    )
    result["objects_drawn"] = len(objects)
    result["side"] = (side or "none").strip().lower() or "none"
    if broker_t is not None:
        result["at_time"] = broker_t
        result["time_label"] = time_label
    return result


async def draw_regime(
    instrument: str,
    granularity: str = "D",
    count: int | None = 250,
    from_time: str | None = None,
    to_time: str | None = None,
    prefix: str = REGIME_PREFIX,
    require_chart_match: bool = True,
) -> dict[str, Any]:
    """Fetch OANDA candles, classify Lien regime, and draw the overlay."""
    from app import oanda_client, regime

    use_count = count
    if from_time and to_time:
        use_count = None
    payload = await oanda_client.get_candles(
        instrument,
        granularity=granularity,
        count=use_count,
        price="M",
        from_time=from_time,
        to_time=to_time,
    )
    bars = oanda_client.candles_to_bars(payload, prefer="mid")
    analysis = regime.analyze_bars(bars)
    analysis["instrument"] = instrument
    analysis["granularity"] = granularity
    if from_time:
        analysis["from_time"] = from_time
    if to_time:
        analysis["to_time"] = to_time
    if use_count is not None:
        analysis["count"] = use_count
    return apply_regime(
        analysis,
        bars,
        instrument,
        granularity,
        prefix=prefix,
        require_chart_match=require_chart_match,
    )


def _run_color(run: dict[str, Any]) -> str:
    if run.get("trend_waning"):
        return "purple"
    regime = run.get("regime")
    direction = run.get("direction")
    if regime == "trend" and direction == "up":
        return "green"
    if regime == "trend" and direction == "down":
        return "red"
    if regime == "range":
        return "orange"
    return "grey"


def _run_label(run: dict[str, Any]) -> str:
    regime = run.get("regime") or "?"
    direction = run.get("direction")
    if run.get("trend_waning"):
        return f"{regime}/waning"
    if direction:
        return f"{regime}/{direction}"
    return str(regime)


def _normalize_walk_show(show: str | None) -> str:
    value = (show or WALK_SHOW_BOTH).strip().lower()
    if value not in WALK_SHOW_CHOICES:
        raise Mt4BridgeError(
            f"Unsupported walk show {show!r}; expected one of {WALK_SHOW_CHOICES}."
        )
    return value


def regime_walk_to_objects(
    result: dict[str, Any],
    offset_seconds: int = 0,
    prefix: str = REGIME_WALK_PREFIX,
    phat_watch: float | None = None,
    instability_watch: float | None = None,
    show: str = WALK_SHOW_BOTH,
) -> list[dict[str, Any]]:
    """Map collapsed causal runs and/or change-watch marks (no BB/SMA).

    ``show`` is ``ranges``, ``markers``, or ``both``. Change-watch arrows only
    when ``p_hat`` or ``instability`` exceed thresholds.
    """
    show = _normalize_walk_show(show)
    draw_ranges = show in (WALK_SHOW_RANGES, WALK_SHOW_BOTH)
    draw_markers = show in (WALK_SHOW_MARKERS, WALK_SHOW_BOTH)
    objects: list[dict[str, Any]] = []
    runs = result.get("runs") or []
    if draw_ranges:
        for n, run in enumerate(runs):
            t1 = parse_rfc3339_utc(run.get("start_time"))
            t2 = parse_rfc3339_utc(run.get("end_time"))
            high, low = run.get("high"), run.get("low")
            if t1 is None or t2 is None or high is None or low is None:
                continue
            bt1 = broker_time(t1, offset_seconds)
            bt2 = broker_time(t2, offset_seconds)
            if bt1 == bt2:
                bt2 = bt1 + 1
            color = _run_color(run)
            objects.append(
                {
                    "name": f"{prefix}run.{n}",
                    "type": "rectangle",
                    "t1": bt1,
                    "p1": float(low),
                    "t2": bt2,
                    "p2": float(high),
                    "color": color,
                    "style": "solid",
                    "width": 1,
                }
            )
            objects.append(
                {
                    "name": f"{prefix}run.{n}.text",
                    "type": "text",
                    "t1": bt1,
                    "p1": float(high),
                    "color": color,
                    "text": _run_label(run),
                    "width": 1,
                }
            )

    summary = result.get("summary") or {}
    counts = summary.get("regime_counts") or {}
    counts_txt = " ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    last_p = summary.get("last_p_hat")
    brier = summary.get("brier")
    objects.append(
        {
            "name": f"{prefix}label",
            "type": "label",
            "x": 8,
            "y": 18,
            "color": "white",
            "text": (
                f"walk steps={summary.get('step_count', 0)} "
                f"runs={summary.get('run_count', 0)} {counts_txt} "
                f"p_hat={last_p if last_p is not None else 'na'} "
                f"brier={brier if brier is not None else 'na'} "
                f"show={show}"
            ),
        }
    )

    if draw_markers:
        if phat_watch is None:
            phat_watch = float(result.get("phat_watch", 0.5))
        if instability_watch is None:
            instability_watch = float(result.get("instability_watch", 0.6))
        phat_watch = float(phat_watch)
        inst_watch = float(instability_watch)
        watch_n = 0
        for step in result.get("steps") or []:
            p_hat = step.get("p_hat")
            instability = step.get("instability")
            watch = (p_hat is not None and float(p_hat) >= phat_watch) or (
                instability is not None and float(instability) >= inst_watch
            )
            if not watch:
                continue
            t1 = parse_rfc3339_utc(step.get("time"))
            if t1 is None:
                continue
            bt1 = broker_time(t1, offset_seconds)
            high = step.get("high")
            if high is None:
                continue
            p_txt = f"{p_hat:.2f}" if p_hat is not None else "na"
            i_txt = f"{instability:.2f}" if instability is not None else "na"
            objects.append(
                {
                    "name": f"{prefix}watch.{watch_n}",
                    "type": "arrow",
                    "t1": bt1,
                    "p1": float(high),
                    "color": "yellow",
                    "arrow_code": 241,
                    "width": 1,
                }
            )
            objects.append(
                {
                    "name": f"{prefix}watch.{watch_n}.text",
                    "type": "text",
                    "t1": bt1,
                    "p1": float(high),
                    "color": "yellow",
                    "text": f"p={p_txt}  i={i_txt}",
                    "width": 1,
                }
            )
            watch_n += 1
    return objects


def apply_regime_walk(
    result: dict[str, Any],
    instrument: str,
    granularity: str,
    prefix: str = REGIME_WALK_PREFIX,
    require_chart_match: bool = True,
    phat_watch: float = 0.5,
    instability_watch: float = 0.6,
    show: str = WALK_SHOW_BOTH,
) -> dict[str, Any]:
    st = status()
    offset = offset_from_heartbeat(st.get("heartbeat"))
    objects = regime_walk_to_objects(
        result,
        offset,
        prefix=prefix,
        phat_watch=phat_watch,
        instability_watch=instability_watch,
        show=show,
    )
    drawn = upsert_objects(
        instrument,
        granularity,
        objects,
        prefix=prefix,
        clear_prefix_first=True,
        require_chart_match=require_chart_match,
    )
    drawn["offset_seconds"] = offset
    drawn["objects_drawn"] = len(objects)
    return drawn


def paper_walk_to_objects(
    trades: list[dict[str, Any]],
    offset_seconds: int = 0,
    prefix: str = TICKET_WALK_PREFIX,
) -> list[dict[str, Any]]:
    """Direction arrow/text plus time-bounded stop and take-profit. No hlines."""
    objects: list[dict[str, Any]] = []
    for n, trade in enumerate(trades):
        entry_t = parse_rfc3339_utc(trade.get("entry_time"))
        entry = trade.get("entry")
        if entry_t is None or entry is None:
            continue
        bt1 = broker_time(entry_t, offset_seconds)
        side = str(trade.get("side") or "none").strip().lower()
        objects.append(
            {
                "name": f"{prefix}{n}.arrow",
                "type": "arrow",
                "t1": bt1,
                "p1": float(entry),
                "color": "orange",
                "arrow_code": 233 if side == "long" else 234,
            }
        )
        if side in ("long", "short"):
            objects.append(
                {
                    "name": f"{prefix}{n}.side",
                    "type": "text",
                    "t1": bt1,
                    "p1": float(entry),
                    "color": "orange",
                    "text": side,
                }
            )
        exit_t = parse_rfc3339_utc(trade.get("exit_time"))
        if exit_t is None:
            bt2 = bt1 + 1
        else:
            bt2 = broker_time(exit_t, offset_seconds)
            if bt2 == bt1:
                bt2 = bt1 + 1
        stop = trade.get("stop")
        if stop is not None:
            objects.append(
                {
                    "name": f"{prefix}{n}.stop",
                    "type": "trend",
                    "t1": bt1,
                    "p1": float(stop),
                    "t2": bt2,
                    "p2": float(stop),
                    "color": "red",
                    "style": "dash",
                    "width": 1,
                    "ray": False,
                }
            )
        target = trade.get("target")
        if target is not None:
            objects.append(
                {
                    "name": f"{prefix}{n}.target",
                    "type": "trend",
                    "t1": bt1,
                    "p1": float(target),
                    "t2": bt2,
                    "p2": float(target),
                    "color": "green",
                    "style": "dash",
                    "width": 1,
                    "ray": False,
                }
            )
        exit_price = trade.get("exit_price")
        if exit_price is None:
            continue
        r_val = trade.get("r_realized")
        try:
            won = r_val is not None and float(r_val) > 0
        except (TypeError, ValueError):
            won = False
        objects.append(
            {
                "name": f"{prefix}{n}.path",
                "type": "trend",
                "t1": bt1,
                "p1": float(entry),
                "t2": bt2,
                "p2": float(exit_price),
                "color": "green" if won else "red",
                "style": "solid",
                "width": 1,
                "ray": False,
            }
        )
    return objects


def apply_paper_walk_tickets(
    trades: list[dict[str, Any]],
    instrument: str,
    granularity: str,
    prefix: str = TICKET_WALK_PREFIX,
    require_chart_match: bool = True,
) -> dict[str, Any]:
    st = status()
    offset = offset_from_heartbeat(st.get("heartbeat"))
    objects = paper_walk_to_objects(trades, offset, prefix=prefix)
    drawn = upsert_objects(
        instrument,
        granularity,
        objects,
        prefix=prefix,
        clear_prefix_first=True,
        require_chart_match=require_chart_match,
    )
    drawn["offset_seconds"] = offset
    drawn["objects_drawn"] = len(objects)
    return drawn
