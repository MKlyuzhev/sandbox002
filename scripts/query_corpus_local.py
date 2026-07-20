#!/usr/bin/env python3
"""Query the ingested corpus locally by calling the RAG pipeline directly (no HTTP).

Mirrors scripts/ingest_corpus_local.py: no FastAPI server required. Uses the
configured Ollama model (see OLLAMA_LLM_MODEL in .env) to answer from retrieved
corpus chunks.

Defaults to fast answers by appending qwen3's /no_think directive; pass --think
to enable the model's reasoning phase (slower, sometimes higher quality).

Examples:
    .venv/bin/python scripts/query_corpus_local.py "What is a head and shoulders pattern?"
    .venv/bin/python scripts/query_corpus_local.py --think --top-k 8 "Explain expectancy"
    .venv/bin/python scripts/query_corpus_local.py --json "Define risk of ruin"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import ollama_client, rag  # noqa: E402
from app.config import settings  # noqa: E402


async def run(args: argparse.Namespace) -> int:
    try:
        resp = await rag.answer(
            args.question, top_k=args.top_k, no_think=not args.think
        )
    except ollama_client.OllamaError as exc:
        print(f"Ollama error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                {
                    "question": args.question,
                    "model": settings.ollama_llm_model,
                    "answer": resp.answer,
                    "sources": [
                        {
                            "source": s.source,
                            "chunk_index": s.chunk_index,
                            "page": s.page,
                            "chunk_type": s.chunk_type,
                            "distance": s.distance,
                        }
                        for s in resp.sources
                    ],
                },
                indent=2,
            )
        )
        return 0

    print(resp.answer)
    if not args.no_sources and resp.sources:
        print("\nSources:")
        for s in resp.sources:
            loc = f"p{s.page}" if s.page is not None else f"#{s.chunk_index}"
            dist = f"{s.distance:.3f}" if s.distance is not None else "n/a"
            print(f"  - {s.source} {loc} (dist {dist})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Query the local corpus with the configured Ollama model (no server)."
    )
    parser.add_argument("question", help="Question to answer from the corpus")
    parser.add_argument(
        "--top-k",
        type=int,
        default=None,
        help=f"Chunks to retrieve (default {settings.top_k})",
    )
    parser.add_argument(
        "--think",
        action="store_true",
        help="Enable model reasoning phase (slower). Default is fast /no_think.",
    )
    parser.add_argument(
        "--no-sources", action="store_true", help="Do not print the source list"
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
