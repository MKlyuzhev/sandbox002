"""Early-warning-only regime-change test: USD majors, 2015-now. No trades.

Causal daily walk. A fire is the first bar of an ``early_warning`` run.
Post-event vs stated ``score`` (risk-of-change, not a probability): Ch.7
flip within 5/10/20 bars, and close-to-close follow-through vs
``direction_to``. Research only; no orders.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent
_S002 = OUT.parents[1]
if str(_S002) not in sys.path:
    sys.path.insert(0, str(_S002))

from agent.lien_pairs import USD_MAJORS  # noqa: E402
from agent.regime_change_walk import walk_early_warning  # noqa: E402
from app import oanda_client  # noqa: E402
from app.walk_fetch import fetch_walk_bars  # noqa: E402

FROM_T = "2015-01-01T00:00:00Z"
TO_T = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
LOOKBACK = 250
HORIZONS = (5, 10, 20)
SCORE_BINS = (
    ("0.00–0.14", 0.0, 0.15),
    ("0.15–0.24", 0.15, 0.25),
    ("0.25–0.34", 0.25, 0.35),
    ("0.35+", 0.35, 1.01),
)

CACHE_DIR = OUT / "cache"
EVENTS_PATH = OUT / "early_warning_events.json"
SUMMARY_PATH = OUT / "early_warning_campaign.json"


def cache_path(instrument: str) -> Path:
    return CACHE_DIR / f"{instrument.lower()}_d.json"


async def load_daily(instrument: str) -> list[dict]:
    path = cache_path(instrument)
    if path.exists():
        bars = json.loads(path.read_text())
        print(f"CACHE {path.name} n={len(bars)} last={bars[-1]['time']}", flush=True)
        return bars
    last_err: Exception | None = None
    for attempt in range(1, 5):
        print(f"FETCH {instrument} D {FROM_T} .. {TO_T} try={attempt}", flush=True)
        try:
            bars = await fetch_walk_bars(
                instrument, "D", FROM_T, TO_T, LOOKBACK, with_ba=False
            )
            path.write_text(json.dumps(bars))
            print(f"WROTE {path.name} n={len(bars)} last={bars[-1]['time']}", flush=True)
            return bars
        except oanda_client.OandaError as exc:
            last_err = exc
            print(f"OANDA {instrument}: {exc}", flush=True)
            await asyncio.sleep(3 * attempt)
    assert last_err is not None
    raise last_err


def _rate(hits: list[bool]) -> float | None:
    if not hits:
        return None
    return round(sum(1 for x in hits if x) / len(hits), 4)


def _mean(xs: list[float]) -> float | None:
    if not xs:
        return None
    return round(sum(xs) / len(xs), 4)


def _bin_label(score: float) -> str:
    for label, lo, hi in SCORE_BINS:
        if lo <= score < hi:
            return label
    return SCORE_BINS[-1][0]


def summarize_events(events: list[dict], horizons: tuple[int, ...] = HORIZONS) -> dict:
    by_pair: dict[str, list] = {}
    for ev in events:
        by_pair.setdefault(ev["instrument"], []).append(ev)

    def horizon_block(rows: list[dict], h: int) -> dict:
        posts = [e["post"].get(str(h)) for e in rows]
        posts = [p for p in posts if p]
        flips = [bool(p["flip"]) for p in posts]
        dir_hits = [bool(p["dir_hit"]) for p in posts if p.get("dir_hit") is not None]
        signed = [float(p["signed_pips"]) for p in posts if p.get("signed_pips") is not None]
        leads = [int(p["lead_time"]) for p in posts if p.get("lead_time") is not None]
        return {
            "n": len(posts),
            "flip_rate": _rate(flips),
            "dir_hit_rate": _rate(dir_hits),
            "mean_signed_pips": _mean(signed),
            "mean_lead_time": _mean([float(x) for x in leads]),
            "n_with_direction": len(dir_hits),
        }

    def pack(rows: list[dict]) -> dict:
        scores = [float(e["score"]) for e in rows if e.get("score") is not None]
        dirs = {}
        for e in rows:
            d = str(e.get("direction_to") or "none")
            dirs[d] = dirs.get(d, 0) + 1
        bins = []
        for label, lo, hi in SCORE_BINS:
            chunk = [e for e in rows if lo <= float(e.get("score") or 0) < hi]
            if not chunk:
                continue
            item = {
                "bin": label,
                "n": len(chunk),
                "mean_score": _mean([float(e["score"]) for e in chunk]),
            }
            for h in horizons:
                item[f"h{h}"] = horizon_block(chunk, h)
            bins.append(item)
        out = {
            "n": len(rows),
            "mean_score": _mean(scores),
            "direction_to": dirs,
            "by_horizon": {str(h): horizon_block(rows, h) for h in horizons},
            "by_score_bin": bins,
        }
        return out

    yearly: dict[str, int] = {}
    for e in events:
        y = str(e.get("time") or "")[:4]
        yearly[y] = yearly.get(y, 0) + 1

    return {
        "all": pack(events),
        "by_pair": {k: pack(v) for k, v in sorted(by_pair.items())},
        "fires_by_year": dict(sorted(yearly.items())),
    }


async def main() -> int:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    all_events: list[dict] = []
    pair_meta: list[dict] = []
    for instrument in USD_MAJORS:
        bars = await load_daily(instrument)
        start_index = LOOKBACK - 1
        print(f"WALK early_warning {instrument} n={len(bars)}", flush=True)
        out = walk_early_warning(
            bars,
            instrument,
            lookback=LOOKBACK,
            start_index=start_index,
            horizons=HORIZONS,
        )
        for ev in out["events"]:
            ev["instrument"] = instrument
            all_events.append(ev)
        pair_meta.append(
            {
                "instrument": instrument,
                "bar_count": len(bars),
                "step_count": out["step_count"],
                "state_counts": out["state_counts"],
                "event_count": out["event_count"],
                "last_daily": bars[-1]["time"] if bars else None,
            }
        )
        print(
            f"DONE {instrument} fires={out['event_count']} "
            f"states={out['state_counts']}",
            flush=True,
        )

    stats = summarize_events(all_events)
    EVENTS_PATH.write_text(json.dumps(all_events, indent=2, default=str) + "\n")
    summary = {
        "engine": "regime_change",
        "mode": "early_warning_only",
        "trades": False,
        "note": (
            "Causal daily early-warning first-fire. No paper tickets. "
            "score is a weighted risk-of-change, not a probability. "
            "Post: Ch.7 flip within horizon; dir_hit is close follow-through "
            "vs direction_to. Tick volume is a proxy. Research only; no orders."
        ),
        "window": f"{FROM_T} to {TO_T}",
        "pairs": list(USD_MAJORS),
        "lookback": LOOKBACK,
        "horizons": list(HORIZONS),
        "pair_meta": pair_meta,
        "event_count": len(all_events),
        **stats,
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, default=str) + "\n")
    print(
        json.dumps(
            {
                "event_count": summary["event_count"],
                "all": summary["all"],
                "by_pair": {
                    k: {"n": v["n"], "mean_score": v["mean_score"], "h5": v["by_horizon"]["5"]}
                    for k, v in summary["by_pair"].items()
                },
            },
            indent=2,
        ),
        flush=True,
    )
    print(f"WROTE {SUMMARY_PATH}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
