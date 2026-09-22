"""Rebuild Chroma source trader-mt4 from episode briefs and accepted rules."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.ingest import ingest_text
from app.store import get_collection
from app.trader_store import DEFAULT_DB_PATH, TraderStore

SOURCE = "trader-mt4"
METADATA = {
    "title": "Trader MT4 journal",
    "author": "trader",
    "asset_class": "fx",
    "topics": "trader,mt4,review,empirical",
    "evidence_level": "empirical",
    "acquisition": "owned",
}


def _collection_delete_source(source: str) -> None:
    collection = get_collection()
    try:
        collection.delete(where={"source": source})
    except Exception:
        # Older/empty collections may reject the filter; ignore.
        pass


def build_corpus_text(store: TraderStore) -> str:
    parts: list[str] = []
    for ep in store.list_episodes():
        review = store.get_review(ep["id"])
        block = ep.get("brief") or ""
        if review:
            block += (
                f"\nreview model_fit={review.get('model_fit')} "
                f"entry_quality={review.get('entry_quality')} "
                f"exit_quality={review.get('exit_quality')} "
                f"reasons={review.get('reasons')}"
            )
        if block.strip():
            parts.append(block.strip())
    for rule in store.list_rules(status="accepted"):
        parts.append(f"accepted rule: {rule.get('text')}")
    return "\n\n".join(parts)


async def ingest_trader(store: TraderStore) -> int:
    text = build_corpus_text(store)
    _collection_delete_source(SOURCE)
    if not text.strip():
        return 0
    return await ingest_text(text, SOURCE, metadata=METADATA)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest trader-mt4 briefs into Chroma.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    store = TraderStore(args.db)
    n = asyncio.run(ingest_trader(store))
    print(json.dumps({"source": SOURCE, "chunks_added": n}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
