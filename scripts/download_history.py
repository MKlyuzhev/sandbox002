#!/usr/bin/env python3
"""Download all available OANDA candle history to data/history (resumable).

One gzipped CSV per instrument and granularity:
``data/history/<INSTRUMENT>/<GRANULARITY>.csv.gz`` with columns
``time,volume,o,h,l,c,bid_o,bid_h,bid_l,bid_c,ask_o,ask_h,ask_l,ask_c``
(mid, bid, ask). Complete bars only, UTC RFC3339 times, OANDA default daily
alignment (17:00 America/New_York). Re-running appends only newer bars.

Research only; no orders.

Examples:
    .venv/bin/python scripts/download_history.py
    .venv/bin/python scripts/download_history.py --instruments EUR_USD --granularities H1,M5
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import oanda_client  # noqa: E402

DEFAULT_INSTRUMENTS = "USD_CHF,GBP_USD,AUD_USD,EUR_JPY,USD_JPY"
DEFAULT_GRANULARITIES = "W,D,H4,H1,M30,M15,M5"
EPOCH = "2000-01-01T00:00:00.000000000Z"
HEADER = "time,volume,o,h,l,c,bid_o,bid_h,bid_l,bid_c,ask_o,ask_h,ask_l,ask_c\n"
RETRIES = 6


def last_time(path: Path) -> str | None:
    if not path.exists():
        return None
    last = None
    with gzip.open(path, "rt") as fh:
        for line in fh:
            if line and not line.startswith("time,"):
                last = line
    return last.split(",", 1)[0] if last else None


def row(candle: dict) -> str | None:
    mid, bid, ask = candle.get("mid"), candle.get("bid"), candle.get("ask")
    if not (mid and bid and ask):
        return None
    cells = [candle["time"], str(candle.get("volume", 0))]
    for side in (mid, bid, ask):
        cells += [side["o"], side["h"], side["l"], side["c"]]
    return ",".join(cells) + "\n"


async def page(instrument: str, granularity: str, cursor: str) -> list[dict]:
    for attempt in range(RETRIES):
        try:
            payload = await oanda_client._candles_request(
                instrument, granularity, "MBA",
                count=oanda_client.MAX_CANDLES_PER_REQUEST, from_time=cursor,
            )
            return list(payload.get("candles") or [])
        except Exception as exc:  # network errors, 429, 5xx
            if "(400)" in str(exc) or "(401)" in str(exc) or attempt == RETRIES - 1:
                raise
            await asyncio.sleep(2 ** attempt)
    return []


async def download(instrument: str, granularity: str, out_dir: Path) -> str:
    path = out_dir / instrument / f"{granularity}.csv.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    resume = last_time(path)
    cursor = resume or EPOCH
    if not path.exists():
        with gzip.open(path, "wt") as fh:
            fh.write(HEADER)
    added, first, last, skipped = 0, None, resume, 0
    started = time.monotonic()
    while True:
        candles = await page(instrument, granularity, cursor)
        rows = []
        for c in candles:
            if not c.get("complete") or (last and c["time"] <= last):
                continue
            line = row(c)
            if line is None:
                skipped += 1
                continue
            rows.append(line)
            first = first or c["time"]
            last = c["time"]
        if rows:
            with gzip.open(path, "at") as fh:
                fh.writelines(rows)
            added += len(rows)
        if not rows or len(candles) < oanda_client.MAX_CANDLES_PER_REQUEST:
            break
        cursor = last
    return (
        f"{instrument} {granularity}: +{added} bars"
        f" ({first or '-'} .. {last or '-'})"
        f"{f', {skipped} without bid/ask skipped' if skipped else ''}"
        f" [{time.monotonic() - started:.0f}s]"
    )


async def run(args: argparse.Namespace) -> int:
    instruments = [s.strip() for s in args.instruments.split(",") if s.strip()]
    grans = [s.strip().upper() for s in args.granularities.split(",") if s.strip()]
    sem = asyncio.Semaphore(args.concurrency)
    failed = 0

    async def one(inst: str, gran: str) -> None:
        nonlocal failed
        async with sem:
            try:
                print(await download(inst, gran, args.out), flush=True)
            except Exception as exc:
                failed += 1
                print(f"{inst} {gran}: FAILED {exc}", flush=True)

    await asyncio.gather(*(one(i, g) for i in instruments for g in grans))
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--instruments", default=DEFAULT_INSTRUMENTS)
    parser.add_argument("--granularities", default=DEFAULT_GRANULARITIES)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "history")
    parser.add_argument("--concurrency", type=int, default=5)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
