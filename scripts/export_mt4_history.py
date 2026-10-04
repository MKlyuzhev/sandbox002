#!/usr/bin/env python3
"""Write data/history candles into MT4 .hst files (format 401).

Times are converted from UTC to OANDA MT4 server time (America/New_York + 7h,
so 00:00 is the 17:00 NY daily close). From OANDA's first bar onward the
OANDA feed replaces the terminal's bars (imported History Center data runs on a
different clock); existing bars are kept only before that first bar and after
its last one. Originals are copied to --backup before being replaced.

MT4 draws trendlines by bar index, so every era must have the same number of
bars per trading week or lines anchored in different years lose their slope:
- Weekend bars (Saturday/Sunday server time; OANDA's old early-Sunday session
  and stray weekend ticks) are folded into the previous Friday / next Monday
  bar on D1 and below.
- OANDA serves one bar per day on intraday granularities before 2005. Those
  bars are replaced by --legacy bars (imported History Center .hst files, which
  are on server time for 2002-2004) where available.

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


def dense_start(bars: dict[int, tuple], period: int) -> int:
    """First time from which an intraday series has more than one bar per day."""
    if period >= 1440:
        return min(bars)
    per_day: dict[int, int] = {}
    for ts in bars:
        per_day[ts // 86400] = per_day.get(ts // 86400, 0) + 1
    day = next((d for d in sorted(per_day) if per_day[d] > 1), None)
    return day * 86400 if day is not None else min(bars)


def fold_weekends(bars: dict[int, tuple]) -> tuple[dict[int, tuple], int]:
    """Merge Saturday bars into the previous bar and Sunday bars into the next."""
    times = sorted(bars)
    out: dict[int, tuple] = {}
    pending: list[tuple] = []  # Sunday bars waiting for the next weekday bar
    folded = 0

    def merge(first: tuple, rest: list[tuple]) -> tuple:
        seq = [first, *rest]
        seq.sort(key=lambda r: r[0])
        return (first[0], seq[0][1], max(r[2] for r in seq), min(r[3] for r in seq),
                seq[-1][4], sum(r[5] for r in seq), first[6], first[7])

    last_kept = None
    for ts in times:
        rec = bars[ts]
        weekday = datetime.fromtimestamp(ts, timezone.utc).weekday()
        if weekday == 5 and last_kept is not None and not pending:
            out[last_kept] = merge(out[last_kept], [rec])
            folded += 1
        elif weekday in (5, 6):
            pending.append(rec)
            folded += 1
        else:
            out[ts] = merge(rec, pending) if pending else rec
            pending = []
            last_kept = ts
    for rec in pending:  # trailing weekend bars with no Monday yet
        out[rec[0]] = rec
        folded -= 1
    return out, folded


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
    parser.add_argument("--legacy", type=Path, default=None,
                        help="folder of imported .hst files used for intraday bars before OANDA's are dense")
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
            dense = dense_start(oanda, period)
            legacy = {}
            if dense > lo and args.legacy:
                _, old = read_hst(args.legacy / dest.name)
                legacy = {t: b for t, b in old.items() if lo <= t < dense}
            if legacy:
                oanda = {t: b for t, b in oanda.items() if t >= dense}
            merged = {**kept, **legacy, **oanda}
            folded = 0
            if period <= 1440:
                merged, folded = fold_weekends(merged)
            if existing:
                backup.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, backup / dest.name)
            write_hst(dest, symbol, period, digits, merged)
            first = datetime.fromtimestamp(min(merged), timezone.utc)
            last = datetime.fromtimestamp(max(merged), timezone.utc)
            sparse = "" if dense == lo or legacy else f", sparse before {datetime.fromtimestamp(dense, timezone.utc):%Y-%m-%d} (no legacy)"
            print(f"{dest.name}: {len(kept)} of {len(existing)} existing kept + {len(legacy)} legacy "
                  f"+ {len(oanda)} OANDA, {folded} weekend bars folded = {len(merged)} "
                  f"({first:%Y-%m-%d} .. {last:%Y-%m-%d %H:%M}){sparse}")
    if not args.check and backup.exists():
        print(f"originals backed up to {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
