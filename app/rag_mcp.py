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

from agent import retrieve  # noqa: E402
from app import store  # noqa: E402

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("rag-knowledge")

mcp = FastMCP("rag-knowledge")


@mcp.tool()
async def search_knowledge(
    query: str, top_k: int = 5, source: str = ""
) -> list[dict]:
    """Retrieve the most relevant corpus chunks for a query (no answer synthesis).

    Embeds the query with the configured Ollama embedding model and runs a
    similarity search over the ingested trading-knowledge corpus. Returns the
    full chunk text plus metadata so the calling agent can reason and cite.

    Args:
        query: Natural-language question or topic, e.g. "position sizing rules".
        top_k: Number of chunks to return (default 5).
        source: Optional corpus id to restrict hits (e.g. "lien-fx"). Empty
            searches the whole collection. Use this for Lien fidelity tests so
            Murphy / Laidi chunks cannot leak into a Lien pin.

    Each result includes: source, chunk_index, page, chunk_type, title, author,
    asset_class, topics, evidence_level, distance (cosine; lower is closer), and
    the full chunk text.
    """
    return await retrieve.search_knowledge(
        query, top_k=top_k, source=source or None
    )


@mcp.tool()
async def get_source_chunk(source: str, chunk_index: int) -> dict:
    """Fetch a single stored chunk by its source and chunk_index for exact citation.

    Prefers ``chunk_type=text`` when a figure caption shares the same index.

    Args:
        source: The document identifier (matches the ``source`` field returned
            by search_knowledge).
        chunk_index: The chunk's index within that source.

    Returns the chunk text and metadata, or an ``error`` field if not found.
    """
    return retrieve.get_source_chunk(source, chunk_index)


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
