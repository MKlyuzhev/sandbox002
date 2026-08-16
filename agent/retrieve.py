"""In-process corpus retrieval (same shape as rag-knowledge search_knowledge)."""

from __future__ import annotations

from typing import Any

from app import ollama_client, store


def default_query(regime: dict[str, Any]) -> str:
    plays = ", ".join(regime.get("allowed_play_classes") or [])
    label = regime.get("regime") or "mixed"
    direction = regime.get("direction") or ""
    return (
        f"Kathy Lien {label} {direction} {plays} "
        "currency market regime filter strategy"
    ).strip()


def _to_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _to_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def search_knowledge(
    query: str,
    top_k: int = 5,
    source: str | None = None,
) -> list[dict[str, Any]]:
    """Embed + Chroma query. Optional metadata filter on ``source``."""
    query_vector = await ollama_client.embed(query)
    collection = store.get_collection()
    kwargs: dict[str, Any] = {
        "query_embeddings": [query_vector],
        "n_results": top_k,
        "include": ["documents", "metadatas", "distances"],
    }
    if source:
        kwargs["where"] = {"source": source}
    try:
        results = collection.query(**kwargs)
    except Exception:
        if not source:
            raise
        kwargs.pop("where", None)
        results = collection.query(**kwargs)

    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    chunks: list[dict[str, Any]] = []
    for doc, meta, dist in zip(documents, metadatas, distances):
        meta = meta or {}
        chunks.append(
            {
                "source": _to_str(meta.get("source")) or "unknown",
                "chunk_index": _to_int(meta.get("chunk_index")),
                "page": _to_int(meta.get("page")),
                "chunk_type": _to_str(meta.get("chunk_type")),
                "title": _to_str(meta.get("title")),
                "distance": float(dist) if dist is not None else None,
                "text": doc,
            }
        )
    return chunks
