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


def _chunk_from_meta(
    doc: Any,
    meta: dict[str, Any] | None,
    dist: Any = None,
) -> dict[str, Any]:
    meta = meta or {}
    return {
        "source": _to_str(meta.get("source")) or "unknown",
        "chunk_index": _to_int(meta.get("chunk_index")),
        "page": _to_int(meta.get("page")),
        "chunk_type": _to_str(meta.get("chunk_type")),
        "title": _to_str(meta.get("title")),
        "author": _to_str(meta.get("author")),
        "asset_class": _to_str(meta.get("asset_class")),
        "topics": _to_str(meta.get("topics")),
        "evidence_level": _to_str(meta.get("evidence_level")),
        "distance": float(dist) if dist is not None else None,
        "text": doc,
    }


def _pick_text_row(
    documents: list[Any],
    metadatas: list[Any],
) -> tuple[Any, dict[str, Any]] | None:
    """Prefer ``chunk_type=text`` when the same index also has a figure caption."""
    rows = [
        (doc, meta or {})
        for doc, meta in zip(documents, metadatas)
    ]
    if not rows:
        return None
    for doc, meta in rows:
        if _to_str(meta.get("chunk_type")) == "text":
            return doc, meta
    return rows[0]


def get_source_chunk(source: str, chunk_index: int) -> dict[str, Any]:
    """Fetch one stored chunk; prefer text over a figure caption at the same index."""
    collection = store.get_collection()
    results = collection.get(
        where={"$and": [{"source": source}, {"chunk_index": chunk_index}]},
        include=["documents", "metadatas"],
    )
    documents = results.get("documents") or []
    metadatas = results.get("metadatas") or []
    picked = _pick_text_row(documents, metadatas)
    if picked is None:
        return {
            "error": f"No chunk found for source={source!r} chunk_index={chunk_index}",
        }
    doc, meta = picked
    return _chunk_from_meta(doc, meta)


async def search_knowledge(
    query: str,
    top_k: int = 5,
    source: str | None = None,
) -> list[dict[str, Any]]:
    """Embed + Chroma query. Optional metadata filter on ``source``.

    If Chroma rejects the ``where`` clause, the unfiltered query still runs, then
    results are post-filtered so other sources cannot leak into a Lien pin.
    """
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
        chunk = _chunk_from_meta(doc, meta, dist)
        if source and chunk.get("source") != source:
            continue
        chunks.append(chunk)
    return chunks
