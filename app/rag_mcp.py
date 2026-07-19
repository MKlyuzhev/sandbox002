"""Read-only knowledge-retrieval MCP server (pure retrieval, no generation).

Exposes the ingested trading-knowledge corpus as MCP tools so any agent (a
frontier model or a local model driving the Cursor Agent) can retrieve and cite
chunks, then reason with its own model. Unlike the FastAPI ``/query`` endpoint
(see app/rag.py ``answer``), this server deliberately does NOT call the LLM to
synthesize an answer -- keeping it model-agnostic.

Run directly (Cursor spawns it this way):

    python app/rag_mcp.py

It reuses the existing ChromaDB collection and Ollama embedding model, so the
FastAPI server does not need to be running.
"""

import logging
import os
import sys
from pathlib import Path

# Cursor may spawn this from any cwd. The app package uses relative paths
# (e.g. Chroma's "./chroma_db") and package-relative imports, so pin both.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
os.chdir(_REPO_ROOT)

from mcp.server.fastmcp import FastMCP  # noqa: E402

from app import ollama_client, store  # noqa: E402

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("rag-knowledge")


def _to_str(value) -> str | None:
    return None if value is None else str(value)


def _to_int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


mcp = FastMCP("rag-knowledge")


@mcp.tool()
async def search_knowledge(query: str, top_k: int = 5) -> list[dict]:
    """Retrieve the most relevant corpus chunks for a query (no answer synthesis).

    Embeds the query with the configured Ollama embedding model and runs a
    similarity search over the ingested trading-knowledge corpus. Returns the
    full chunk text plus metadata so the calling agent can reason and cite.

    Args:
        query: Natural-language question or topic, e.g. "position sizing rules".
        top_k: Number of chunks to return (default 5).

    Each result includes: source, chunk_index, page, chunk_type, title, author,
    asset_class, topics, evidence_level, distance (cosine; lower is closer), and
    the full chunk text.
    """
    query_vector = await ollama_client.embed(query)
    collection = store.get_collection()
    results = collection.query(
        query_embeddings=[query_vector],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )

    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    chunks: list[dict] = []
    for doc, meta, dist in zip(documents, metadatas, distances):
        meta = meta or {}
        chunks.append(
            {
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
        )
    return chunks


@mcp.tool()
async def get_source_chunk(source: str, chunk_index: int) -> dict:
    """Fetch a single stored chunk by its source and chunk_index for exact citation.

    Args:
        source: The document identifier (matches the ``source`` field returned
            by search_knowledge).
        chunk_index: The chunk's index within that source.

    Returns the chunk text and metadata, or an ``error`` field if not found.
    """
    collection = store.get_collection()
    results = collection.get(
        where={"$and": [{"source": source}, {"chunk_index": chunk_index}]},
        include=["documents", "metadatas"],
    )
    documents = results.get("documents") or []
    metadatas = results.get("metadatas") or []
    if not documents:
        return {
            "error": f"No chunk found for source={source!r} chunk_index={chunk_index}",
        }
    meta = metadatas[0] or {}
    return {
        "source": _to_str(meta.get("source")) or source,
        "chunk_index": _to_int(meta.get("chunk_index")),
        "page": _to_int(meta.get("page")),
        "chunk_type": _to_str(meta.get("chunk_type")),
        "title": _to_str(meta.get("title")),
        "author": _to_str(meta.get("author")),
        "text": documents[0],
    }


@mcp.tool()
async def corpus_stats() -> dict:
    """Return corpus orientation: total chunk count and the distinct source
    documents currently ingested."""
    collection = store.get_collection()
    total = store.count()
    sources: list[str] = []
    try:
        meta_only = collection.get(include=["metadatas"])
        seen = {
            str(m.get("source"))
            for m in (meta_only.get("metadatas") or [])
            if m and m.get("source") is not None
        }
        sources = sorted(seen)
    except Exception as exc:  # orientation is best-effort
        logger.warning("corpus_stats: could not enumerate sources: %s", exc)
    return {"chunk_count": total, "sources": sources}


if __name__ == "__main__":
    logger.info(
        "Starting rag-knowledge MCP server (chroma=%s, chunks=%s)",
        store.settings.chroma_persist_dir,
        store.count(),
    )
    mcp.run()
