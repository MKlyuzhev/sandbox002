"""Cluster review failure modes into hypothesis rules. Never auto-accept."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.trader_episodes import freeze_objects
from app.trader_store import DEFAULT_DB_PATH, TraderStore

MIN_CLUSTER = 2


def _failure_mode(review: dict) -> str:
    reasons = review.get("reasons") or []
    for key in (
        "structure_broke_immediately",
        "chase",
        "stop_widened",
        "stop_inside_structure",
        "mfe_left_on_table",
        "target_hit",
        "stop_hit",
    ):
        if key in reasons:
            return key
    scores = [
        ("model", review.get("model_fit")),
        ("entry", review.get("entry_quality")),
        ("exit", review.get("exit_quality")),
    ]
    numbered = [(name, s) for name, s in scores if s is not None]
    if not numbered:
        return "unscored"
    name, _ = min(numbered, key=lambda item: item[1])
    return f"weak_{name}"


def _roles(objects: list[dict]) -> str:
    roles = sorted({str(o.get("role") or "unknown") for o in objects})
    return ",".join(roles)


def distill(store: TraderStore, *, min_cluster: int = MIN_CLUSTER) -> list[dict]:
    buckets: dict[str, list[str]] = defaultdict(list)
    for ep in store.list_episodes(has_fill=True):
        review = store.get_review(ep["id"])
        if not review:
            continue
        order = store.get_order(int(ep["ticket"]))
        side = (order or {}).get("type") or ""
        key = "|".join(
            [
                str(ep.get("symbol")),
                str(ep.get("timeframe")),
                _roles(freeze_objects(ep)),
                side,
                _failure_mode(review),
            ]
        )
        buckets[key].append(ep["id"])

    created: list[dict] = []
    for key, ids in buckets.items():
        if len(ids) < min_cluster:
            continue
        mode = key.split("|")[-1]
        text = (
            f"Hypothesis from {len(ids)} episodes [{key}]: "
            f"recurring failure mode {mode}."
        )
        rid = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        rule = {
            "id": rid,
            "status": "hypothesis",
            "cluster_key": key,
            "text": text,
            "failure_mode": mode,
        }
        store.upsert_rule(rule)
        created.append(rule)
    return created


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Propose trader rules from review clusters (hypotheses only)."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--min-cluster", type=int, default=MIN_CLUSTER)
    args = parser.parse_args()
    store = TraderStore(args.db)
    rules = distill(store, min_cluster=args.min_cluster)
    print(json.dumps({"hypotheses": rules}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
