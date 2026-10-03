#!/usr/bin/env python3
"""Write data/history candles into MT4 .hst files (format 401).

Times are converted from UTC to OANDA MT4 server time (America/New_York + 7h,
so 00:00 is the 17:00 NY daily close). From OANDA's first bar onward the
OANDA feed replaces the terminal's bars (imported History Center data runs on a
different clock); existing bars are kept only before that first bar and after
its last one. Originals are copied to --backup before being replaced.

Close MT4 first: a running terminal holds open charts in memory and rewrites
their .hst files, discarding the import.

Research only; no orders.

Examples:
    .venv/bin/python scripts/export_mt4_history.py --check
    .venv/bin/python scripts/export_mt4_history.py
"""

from __future__ import annotations

import argparse
import gzip
import os
import shutil
import struct
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import mt4_bridge  # noqa: E402

PERIODS = {"W": 10080, "D": 1440, "H4": 240, "H1": 60, "M30": 30, "M15": 15, "M5": 5}
NY = ZoneInfo("America/New_York")
HEADER = struct.Struct("<i64s12siiii52s")
RECORD = struct.Struct("<qddddqiq")  # time, open, high, low, close, tick vol, spread, real vol


def broker_ts(utc_rfc3339: str) -> int:
    utc = datetime.strptime(utc_rfc3339[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    local = utc.astimezone(NY).replace(tzinfo=None) + timedelta(hours=7)
    return int(local.replace(tzinfo=timezone.utc).timestamp())


def read_hst(path: Path) -> tuple[int, dict[int, tuple]]:
    """Digits and {time: record} from a v401 file (empty if missing)."""
    if not path.exists():
        return 0, {}
    data = path.read_bytes()
    version, _c, _s, _p, digits, *_ = HEADER.unpack_from(data, 0)
    if version != 401:
        raise SystemExit(f"{path.name}: unsupported .hst version {version}")
    bars = {}
    for off in range(HEADER.size, len(data) - RECORD.size + 1, RECORD.size):
        rec = RECORD.unpack_from(data, off)
        bars[rec[0]] = rec
    return digits, bars


def read_oanda(path: Path, period: int, digits: int) -> dict[int, tuple]:
    point = 10.0 ** -digits
    bars = {}
    with gzip.open(path, "rt") as fh:
        next(fh)
        for line in fh:
            t, vol, o, h, l, c, _bo, _bh, _bl, bc, _ao, _ah, _al, ac = line.rstrip().split(",")
            ts = broker_ts(t)
            if period == 10080:
                ts += 86400  # OANDA week opens Sat 00:00 server time; MT4 W1 bars are stamped Sunday
            spread = max(0, round((float(ac) - float(bc)) / point))
            bars[ts] = (ts, float(o), float(h), float(l), float(c), int(vol), spread, 0)
    return bars


def write_hst(path: Path, symbol: str, period: int, digits: int, bars: dict[int, tuple]) -> None:
    tmp = path.with_suffix(".hst.tmp")
    with open(tmp, "wb") as fh:
        fh.write(HEADER.pack(401, b"(C)opyright 2003, MetaQuotes Software Corp.",
                             symbol.encode(), period, digits, 0, 0, b""))
        for ts in sorted(bars):
            fh.write(RECORD.pack(*bars[ts]))
    os.replace(tmp, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--history", type=Path, default=ROOT / "data" / "history")
    parser.add_argument("--server", default="OANDA-Demo-1")
    parser.add_argument("--hst-dir", type=Path, default=None,
                        help="write here instead of the terminal's history/<server> folder")
    parser.add_argument("--backup", type=Path, default=None)
    parser.add_argument("--check", action="store_true",
                        help="compare overlapping bars with the existing files; write nothing")
    args = parser.parse_args()
    hst_dir = args.hst_dir or mt4_bridge.inbox_dir().parents[2] / "history" / args.server
    if not hst_dir.is_dir():
        raise SystemExit(f"no MT4 history folder {hst_dir}")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = args.backup or ROOT.parent / f"sandbox002-mt4-history-backup-{stamp}"

    for inst_dir in sorted(p for p in args.history.iterdir() if p.is_dir()):
        symbol = mt4_bridge.map_symbol(inst_dir.name)
        for gran, period in PERIODS.items():
            src = inst_dir / f"{gran}.csv.gz"
            if not src.exists():
                continue
            dest = hst_dir / f"{symbol}{period}.hst"
            digits, existing = read_hst(dest)
            digits = digits or (3 if symbol.endswith("JPY") else 5)
            oanda = read_oanda(src, period, digits)
            common = existing.keys() & oanda.keys()
            if args.check:
                diffs = [abs(existing[t][4] - oanda[t][4]) for t in common]
                med = sorted(diffs)[len(diffs) // 2] if diffs else None
                print(f"{dest.name}: existing {len(existing)}, oanda {len(oanda)}, "
                      f"overlap {len(common)}, median |close diff| {med}")
                continue
            lo, hi = min(oanda), max(oanda)
            kept = {t: b for t, b in existing.items() if t < lo or t > hi}
            merged = {**kept, **oanda}
            if existing:
                backup.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, backup / dest.name)
            write_hst(dest, symbol, period, digits, merged)
            first = datetime.fromtimestamp(min(merged), timezone.utc)
            last = datetime.fromtimestamp(max(merged), timezone.utc)
            print(f"{dest.name}: {len(kept)} of {len(existing)} existing kept + {len(oanda)} OANDA "
                  f"= {len(merged)} ({first:%Y-%m-%d} .. {last:%Y-%m-%d %H:%M})")
    if not args.check and backup.exists():
        print(f"originals backed up to {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
