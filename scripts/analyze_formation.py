#!/usr/bin/env python3
"""Analyze early trendlines / H&S formation from OANDA candles (no MCP).

By default prints geometry JSON only (no local model). Pass ``--brief`` to
retrieve a book checklist and ask the configured Ollama model to interpret
the stage — see docs/FORMATION_ANALYSIS.md.

Examples:
    .venv/bin/python scripts/analyze_formation.py
    .venv/bin/python scripts/analyze_formation.py --instrument GBP_USD --count 150
    .venv/bin/python scripts/analyze_formation.py --brief
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import oanda_client, ollama_client, patterns, store  # noqa: E402
from app.config import settings  # noqa: E402
from app.rag import _strip_think  # noqa: E402

_STAGE_QUERIES = {
    "none": "trendline support resistance early formation",
    "left_shoulder": "head and shoulders left shoulder formation early",
    "head": "head and shoulders head formation volume",
    "right_shoulder_forming": "head and shoulders right shoulder volume dull rally",
    "neckline_tentative": "tentative neckline head and shoulders confirmation volume",
    "confirmed_break": "head and shoulders neckline break measurement objective",
    "invalidated": "head and shoulders failed false confirmation",
}


async def _retrieve_checklist(stage: str, top_k: int = 4) -> list[dict]:
    query = _STAGE_QUERIES.get(stage, "head and shoulders reversal neckline")
    vector = await ollama_client.embed(query)
    collection = store.get_collection()
    results = collection.query(
        query_embeddings=[vector],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]
    chunks: list[dict] = []
    for doc, meta, dist in zip(docs, metas, dists):
        meta = meta or {}
        chunks.append(
            {
                "source": meta.get("source"),
                "chunk_index": meta.get("chunk_index"),
                "distance": float(dist) if dist is not None else None,
                "text": doc,
            }
        )
    return chunks


async def _brief(analysis: dict, think: bool) -> str:
    hs = analysis.get("hs") or {}
    stage = hs.get("stage", "none")
    chunks = await _retrieve_checklist(stage)
    context = "\n\n".join(
        f"[{i + 1}] ({c.get('source')} #{c.get('chunk_index')})\n{c.get('text', '')}"
        for i, c in enumerate(chunks)
    )
    system = (
        "You are a research assistant for technical analysis. "
        "Interpret the provided formation JSON using only the book context. "
        "Do not invent prices, trendlines, or coordinates. "
        "Do not give trade orders. Be concise."
    )
    if not think:
        system += "\n\n/no_think"
    user = (
        f"Formation analysis JSON:\n{json.dumps(analysis['hs'], indent=2)}\n\n"
        f"Last close: {analysis.get('last_close')} at {analysis.get('last_time')}\n\n"
        f"Book context:\n{context}\n\n"
        "Write a short research brief: current stage meaning, what to watch next, "
        "and cite sources by book/chunk from the context."
    )
    raw = await ollama_client.chat(system, user)
    return _strip_think(raw)


async def run(args: argparse.Namespace) -> int:
    try:
        payload = await oanda_client.get_candles(
            args.instrument,
            granularity=args.granularity,
            count=args.count,
            price="M",
        )
    except oanda_client.OandaError as exc:
        print(f"OANDA error: {exc}", file=sys.stderr)
        return 2

    bars = oanda_client.candles_to_bars(payload, prefer="mid")
    if len(bars) < 20:
        print(f"Not enough bars ({len(bars)}); need more history.", file=sys.stderr)
        return 1

    try:
        analysis = patterns.analyze_bars(
            bars,
            swing_left=args.swing_left,
            swing_right=args.swing_right,
            max_lines=args.max_lines,
            break_frac=args.break_frac,
        )
    except patterns.PatternError as exc:
        print(f"Pattern error: {exc}", file=sys.stderr)
        return 1

    analysis["instrument"] = args.instrument
    analysis["granularity"] = args.granularity
    analysis["model"] = settings.ollama_llm_model

    if args.brief:
        try:
            analysis["brief"] = await _brief(analysis, think=args.think)
        except ollama_client.OllamaError as exc:
            print(f"Ollama error: {exc}", file=sys.stderr)
            return 2

    print(json.dumps(analysis, indent=2, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Early trendline / H&S formation analysis (geometry; optional local brief)."
    )
    parser.add_argument("--instrument", default="EUR_USD")
    parser.add_argument("--granularity", default="H1")
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--swing-left", type=int, default=3)
    parser.add_argument("--swing-right", type=int, default=3)
    parser.add_argument("--max-lines", type=int, default=5)
    parser.add_argument(
        "--break-frac",
        type=float,
        default=0.001,
        help="FX neckline break fraction of price (default 0.001)",
    )
    parser.add_argument(
        "--brief",
        action="store_true",
        help="Retrieve corpus checklist and ask the local model to interpret (opt-in)",
    )
    parser.add_argument(
        "--think",
        action="store_true",
        help="With --brief, enable model thinking (slower). Default is /no_think.",
    )
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
