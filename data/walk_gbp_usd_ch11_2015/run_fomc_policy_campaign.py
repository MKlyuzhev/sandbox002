"""Judge Ch.11 FOMC policies A/B/C on the existing GBP_USD paper book.

Policy A (skip new tickets on the scheduled statement London date) is the
documented perspective hygiene overlay — not encoded in waiting_deal.
Policies B (flatten) and C (next London date only) were judged and rejected
as engine rules. Research only; no orders.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

OUT = Path(__file__).resolve().parent
_S002 = OUT.parents[1]
if str(_S002) not in sys.path:
    sys.path.insert(0, str(_S002))

from agent.paper_walk import check_exit  # noqa: E402
from app.risk import apply_r_to_equity, r_multiple  # noqa: E402
from app.session_clock import (  # noqa: E402
    TZ_NY,
    parse_bar_datetime,
    tag_bar,
    waiting_deal_state,
)

TRADES_PATH = OUT / "waiting_deal_trades.json"
M15_PATH = OUT / "gbp_usd_m15.json"
CAL_PATH = OUT / "fomc_calendar.json"
SUMMARY_PATH = OUT / "fomc_policy_campaign.json"
AUDIT_PATH = OUT / "fomc_policy_trades.json"

START_EQUITY = 10_000.0
RISK_FRAC = 0.02
PIP = 0.0001
M15 = timedelta(minutes=15)
CLOCK = "frankfurt"


def parse_hhmm(hhmm: str) -> time:
    hh, mm = hhmm.split(":")
    return time(int(hh), int(mm))


def next_weekday(d: date) -> date:
    step = 1
    nxt = d + timedelta(days=step)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def chain_equity(rs: list[float], starting: float = START_EQUITY) -> dict:
    eq = starting
    peak = starting
    max_dd = 0.0
    max_dd_frac = 0.0
    wins = sum(1 for r in rs if r > 0)
    for r in rs:
        _, eq = apply_r_to_equity(eq, RISK_FRAC, r)
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd
            max_dd_frac = dd / peak if peak else 0.0
    n = len(rs)
    return {
        "n": n,
        "starting_equity": starting,
        "ending_equity": round(eq, 4),
        "peak_equity": round(peak, 4),
        "max_drawdown": round(max_dd, 4),
        "max_drawdown_frac": round(max_dd_frac, 4),
        "wins": wins,
        "win_rate": round(wins / n, 4) if n else None,
        "sum_r": round(sum(rs), 4) if n else None,
        "mean_r": round(sum(rs) / n, 4) if n else None,
    }


def slice_stats(rows: list[dict], r_key: str = "r_realized") -> dict:
    rs = [float(t[r_key]) for t in rows if t.get(r_key) is not None]
    n = len(rs)
    sides = Counter(t.get("side") for t in rows)
    exits = Counter(t.get("exit_status") for t in rows)
    plays = Counter(t.get("play_class") for t in rows)
    return {
        "n": n,
        "wins": sum(1 for r in rs if r > 0),
        "win_rate": round(sum(1 for r in rs if r > 0) / n, 4) if n else None,
        "mean_r": round(sum(rs) / n, 4) if n else None,
        "sum_r": round(sum(rs), 4) if n else None,
        "long": sides.get("long", 0),
        "short": sides.get("short", 0),
        "exits": dict(exits),
        "play_class": dict(plays),
    }


def load_events() -> list[dict]:
    payload = json.loads(CAL_PATH.read_text())
    events = []
    for raw in payload["events"]:
        ny_d = date.fromisoformat(raw["ny_date"])
        hhmm = raw["ny_time"]
        dt_ny = datetime.combine(ny_d, parse_hhmm(hhmm), tzinfo=TZ_NY)
        dt_utc = dt_ny.astimezone(timezone.utc)
        tagged = tag_bar(dt_utc)
        london_date = date.fromisoformat(tagged["london_date"]) if tagged else ny_d
        events.append(
            {
                **raw,
                "dt_utc": dt_utc,
                "dt_utc_s": dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "london_date": london_date.isoformat(),
                "next_london_date": next_weekday(london_date).isoformat(),
                "ny_weekday": ny_d.weekday(),
                "london_weekday": london_date.weekday(),
            }
        )
    return events


def flatten_index(bars: list[dict], statement: datetime) -> int | None:
    """Last M15 whose close is strictly before the statement."""
    found = None
    for i, bar in enumerate(bars):
        dt = parse_bar_datetime(bar.get("time"))
        if dt is None:
            continue
        if dt + M15 < statement:
            found = i
        elif dt >= statement:
            break
    return found


def counterfactual_flatten(trade: dict, bars: list[dict], statement: datetime) -> dict:
    entry_i = int(trade["entry_index"])
    side = trade["side"]
    stop = float(trade["stop"])
    target = float(trade["target"])
    entry = float(trade["entry"])
    flat_i = flatten_index(bars, statement)
    if flat_i is None or flat_i <= entry_i:
        return {
            "flatten_possible": False,
            "flatten_reason": "no M15 close strictly before statement after entry",
        }
    for i in range(entry_i + 1, flat_i + 1):
        hit = check_exit(side, stop, target, bars[i])
        if hit is not None:
            status, price = hit
            r = round(r_multiple(entry, stop, price), 4)
            return {
                "flatten_possible": True,
                "flatten_hit_before": True,
                "flatten_exit_status": status,
                "flatten_exit_index": i,
                "flatten_exit_time": bars[i]["time"],
                "flatten_exit_price": price,
                "flatten_r": r,
            }
    price = float(bars[flat_i]["close"])
    r = round(r_multiple(entry, stop, price), 4)
    return {
        "flatten_possible": True,
        "flatten_hit_before": False,
        "flatten_exit_status": "fomc_flatten",
        "flatten_exit_index": flat_i,
        "flatten_exit_time": bars[flat_i]["time"],
        "flatten_exit_price": price,
        "flatten_r": r,
    }


def day_micro(bars: list[dict], last_i_by_date: dict[str, int], london_date: str) -> dict:
    last_i = last_i_by_date.get(london_date)
    if last_i is None:
        return {"london_date": london_date, "in_sample": False}
    state = waiting_deal_state(
        bars, pip=PIP, hunt_pips=25.0, clock=CLOCK, end_index=last_i
    )
    return {
        "london_date": london_date,
        "in_sample": True,
        "power_hour_complete": bool(state.get("power_hour_complete")),
        "range_pips": state.get("range_pips"),
        "hunt_side": state.get("hunt_side"),
        "reversed": bool(state.get("reversed")),
    }


def main() -> int:
    cal = json.loads(CAL_PATH.read_text())
    events = load_events()
    trades = json.loads(TRADES_PATH.read_text())
    bars = json.loads(M15_PATH.read_text())
    print(f"trades={len(trades)} m15={len(bars)} events={len(events)}", flush=True)

    last_i_by_date: dict[str, int] = {}
    for i, bar in enumerate(bars):
        tagged = tag_bar(bar.get("time"))
        if tagged is None or tagged["weekend"]:
            continue
        last_i_by_date[tagged["london_date"]] = i

    scheduled = [e for e in events if e["kind"] == "scheduled"]
    sched_london = {e["london_date"]: e for e in scheduled}
    next_london = {e["next_london_date"]: e for e in scheduled}

    last_bar_t = parse_bar_datetime(bars[-1]["time"])
    first_bar_t = parse_bar_datetime(bars[0]["time"])
    in_window = [
        e
        for e in scheduled
        if first_bar_t is not None
        and last_bar_t is not None
        and first_bar_t <= e["dt_utc"] <= last_bar_t + timedelta(hours=24)
    ]

    annotated: list[dict] = []
    for t in trades:
        entry_dt = parse_bar_datetime(t["entry_time"])
        exit_dt = parse_bar_datetime(t["exit_time"])
        entry_tag = tag_bar(t["entry_time"])
        exit_tag = tag_bar(t["exit_time"])
        entry_ld = entry_tag["london_date"] if entry_tag else None
        row = {
            **{k: t[k] for k in (
                "run_id", "entry_time", "exit_time", "side", "play_class",
                "entry", "stop", "target", "exit_status", "exit_price",
                "r_realized", "entry_index", "exit_index",
            )},
            "entry_london_date": entry_ld,
            "exit_london_date": exit_tag["london_date"] if exit_tag else None,
            "policy_a_skip": entry_ld in sched_london,
            "policy_c_next_day": entry_ld in next_london,
            "open_at_scheduled": False,
        }
        if entry_ld in sched_london:
            row["entry_event"] = {
                "fed_id": sched_london[entry_ld]["fed_id"],
                "kind": "scheduled",
                "dt_utc": sched_london[entry_ld]["dt_utc_s"],
            }
        if entry_ld in next_london:
            row["next_day_of"] = {
                "fed_id": next_london[entry_ld]["fed_id"],
                "dt_utc": next_london[entry_ld]["dt_utc_s"],
            }
        spanning = None
        for e in scheduled:
            if entry_dt is None or exit_dt is None:
                continue
            if entry_dt < e["dt_utc"] < exit_dt:
                spanning = e
                break
        if spanning is not None:
            row["open_at_scheduled"] = True
            row["span_event"] = {
                "fed_id": spanning["fed_id"],
                "dt_utc": spanning["dt_utc_s"],
                "london_date": spanning["london_date"],
            }
            row.update(counterfactual_flatten(t, bars, spanning["dt_utc"]))
        annotated.append(row)

    base_rs = [float(t["r_realized"]) for t in annotated]
    baseline = chain_equity(base_rs)

    skip_kept = [t for t in annotated if not t["policy_a_skip"]]
    policy_a = chain_equity([float(t["r_realized"]) for t in skip_kept])
    policy_a["dropped"] = sum(1 for t in annotated if t["policy_a_skip"])

    b_rs = []
    b_replaced = 0
    b_hit_before = 0
    for t in annotated:
        if t.get("open_at_scheduled") and t.get("flatten_possible") and not t.get(
            "flatten_hit_before"
        ):
            b_rs.append(float(t["flatten_r"]))
            b_replaced += 1
        else:
            if t.get("flatten_hit_before"):
                b_hit_before += 1
            b_rs.append(float(t["r_realized"]))
    policy_b = chain_equity(b_rs)
    policy_b["replaced_with_flatten"] = b_replaced
    policy_b["hit_stop_or_target_before_flatten"] = b_hit_before

    ab_rs = []
    ab_dropped = 0
    ab_replaced = 0
    for t in annotated:
        if t["policy_a_skip"]:
            ab_dropped += 1
            continue
        if t.get("open_at_scheduled") and t.get("flatten_possible") and not t.get(
            "flatten_hit_before"
        ):
            ab_rs.append(float(t["flatten_r"]))
            ab_replaced += 1
        else:
            ab_rs.append(float(t["r_realized"]))
    policy_ab = chain_equity(ab_rs)
    policy_ab["dropped"] = ab_dropped
    policy_ab["replaced_with_flatten"] = ab_replaced

    a_entries = [t for t in annotated if t["policy_a_skip"]]
    c_entries = [t for t in annotated if t["policy_c_next_day"]]
    rest = [
        t
        for t in annotated
        if not t["policy_a_skip"] and not t["policy_c_next_day"]
    ]
    spanning_rows = [t for t in annotated if t.get("open_at_scheduled")]
    flatten_rows = [
        t
        for t in spanning_rows
        if t.get("flatten_possible") and not t.get("flatten_hit_before")
    ]

    flatten_delta = []
    for t in flatten_rows:
        flatten_delta.append(
            {
                "entry_time": t["entry_time"],
                "side": t["side"],
                "play_class": t["play_class"],
                "orig_exit": t["exit_status"],
                "orig_r": t["r_realized"],
                "flatten_r": t["flatten_r"],
                "delta_r": round(float(t["flatten_r"]) - float(t["r_realized"]), 4),
                "fed_id": t["span_event"]["fed_id"],
            }
        )

    fomc_days_micro = []
    next_days_micro = []
    hunts = reverses = days_ok = 0
    next_hunts = next_rev = next_ok = 0
    for e in in_window:
        m = day_micro(bars, last_i_by_date, e["london_date"])
        fomc_days_micro.append({**m, "fed_id": e["fed_id"]})
        if m.get("in_sample") and m.get("power_hour_complete") and m.get("range_pips") is not None:
            days_ok += 1
            if m.get("hunt_side"):
                hunts += 1
            if m.get("reversed"):
                reverses += 1
        n = day_micro(bars, last_i_by_date, e["next_london_date"])
        next_days_micro.append({**n, "fed_id": e["fed_id"]})
        if n.get("in_sample") and n.get("power_hour_complete") and n.get("range_pips") is not None:
            next_ok += 1
            if n.get("hunt_side"):
                next_hunts += 1
            if n.get("reversed"):
                next_rev += 1

    def rate(num: int, den: int) -> float | None:
        return round(num / den, 3) if den else None

    unscheduled = [e for e in events if e["kind"] == "unscheduled"]
    unsched_stats = []
    for e in unscheduled:
        hits = [
            t
            for t in annotated
            if t.get("entry_london_date") == e["london_date"]
            or (
                parse_bar_datetime(t["entry_time"])
                and parse_bar_datetime(t["exit_time"])
                and parse_bar_datetime(t["entry_time"])
                < e["dt_utc"]
                < parse_bar_datetime(t["exit_time"])
            )
        ]
        unsched_stats.append(
            {
                "fed_id": e["fed_id"],
                "ny_date": e["ny_date"],
                "ny_time": e["ny_time"],
                "london_date": e["london_date"],
                "n_touching_trades": len(hits),
                "mean_r": (
                    round(
                        sum(float(t["r_realized"]) for t in hits) / len(hits), 4
                    )
                    if hits
                    else None
                ),
            }
        )

    year_a = defaultdict(list)
    for t in a_entries:
        year_a[t["entry_time"][:4]].append(t)

    summary = {
        "instrument": "GBP_USD",
        "engine": "waiting_deal",
        "chapter": 11,
        "note": (
            "Counterfactual FOMC policies on the existing close-fill Ch.11 book. "
            "Does not recode waiting_deal. Scheduled statements only for A/B/C; "
            "unscheduled 2020 listed separately. Flatten = last M15 close strictly "
            "before the statement; stop/target still win if they trade first. "
            "Research only; no orders."
        ),
        "calendar_source": cal["note"],
        "n_scheduled_in_window": len(in_window),
        "n_unscheduled": len(unscheduled),
        "baseline": baseline,
        "policy_a_skip_entry_day": {
            **policy_a,
            "entry_subset": slice_stats(a_entries),
            "kept_subset": slice_stats(skip_kept),
        },
        "policy_b_flatten": {
            **policy_b,
            "open_across_statement": slice_stats(spanning_rows),
            "flatten_mark": slice_stats(
                [
                    {**t, "r_realized": t["flatten_r"], "exit_status": "fomc_flatten"}
                    for t in flatten_rows
                ]
            ),
            "mean_delta_r": (
                round(
                    sum(d["delta_r"] for d in flatten_delta) / len(flatten_delta), 4
                )
                if flatten_delta
                else None
            ),
            "sum_delta_r": (
                round(sum(d["delta_r"] for d in flatten_delta), 4)
                if flatten_delta
                else None
            ),
        },
        "policy_a_plus_b": policy_ab,
        "policy_c_next_london_date": {
            "entry_subset": slice_stats(c_entries),
            "rest_excluding_fomc_and_next": slice_stats(rest),
            "note": (
                "C is not 'only trade these days'. It is the Thursday-after "
                "(next weekday) book vs the rest, after skipping the statement "
                "London date."
            ),
        },
        "microstructure": {
            "fomc_london_dates": {
                "days": days_ok,
                "hunts": hunts,
                "reverses": reverses,
                "hunt_rate": rate(hunts, days_ok),
                "reverse_rate": rate(reverses, days_ok),
                "reverse_rate_given_hunt": rate(reverses, hunts),
            },
            "next_london_dates": {
                "days": next_ok,
                "hunts": next_hunts,
                "reverses": next_rev,
                "hunt_rate": rate(next_hunts, next_ok),
                "reverse_rate": rate(next_rev, next_ok),
                "reverse_rate_given_hunt": rate(next_rev, next_hunts),
            },
            "all_days_campaign": {
                "days_with_power_hour": 3031,
                "hunt_rate": 0.894,
                "reverse_rate": 0.209,
                "reverse_rate_given_hunt": 0.234,
            },
        },
        "unscheduled_2020": unsched_stats,
        "flatten_audit_n": len(flatten_delta),
        "a_entries_by_year": {
            y: slice_stats(v) for y, v in sorted(year_a.items())
        },
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, default=str))
    AUDIT_PATH.write_text(
        json.dumps(
            {
                "flatten_delta": flatten_delta,
                "a_entries": [
                    {
                        "entry_time": t["entry_time"],
                        "side": t["side"],
                        "play_class": t["play_class"],
                        "r_realized": t["r_realized"],
                        "exit_status": t["exit_status"],
                        "fed_id": (t.get("entry_event") or {}).get("fed_id"),
                    }
                    for t in a_entries
                ],
                "c_entries": [
                    {
                        "entry_time": t["entry_time"],
                        "side": t["side"],
                        "r_realized": t["r_realized"],
                        "fed_id": (t.get("next_day_of") or {}).get("fed_id"),
                    }
                    for t in c_entries
                ],
            },
            indent=2,
        )
    )
    print(json.dumps({
        "baseline": baseline,
        "policy_a": policy_a,
        "policy_b": policy_b,
        "policy_ab": policy_ab,
        "a_subset": summary["policy_a_skip_entry_day"]["entry_subset"],
        "c_subset": summary["policy_c_next_london_date"]["entry_subset"],
        "span": summary["policy_b_flatten"]["open_across_statement"],
        "flatten_mark": summary["policy_b_flatten"]["flatten_mark"],
        "micro": summary["microstructure"],
        "unscheduled": unsched_stats,
    }, indent=2), flush=True)
    print(f"WROTE {SUMMARY_PATH}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
