import re

from . import ollama_client, store
from .config import settings


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split text into word-based chunks with overlap.

    chunk_size and overlap are measured in whitespace-delimited tokens,
    a close-enough proxy for model tokens for retrieval purposes.
    """
    text = text.strip()
    if not text:
        return []

    words = re.split(r"\s+", text)
    if len(words) <= chunk_size:
        return [" ".join(words)]

    chunks: list[str] = []
    step = max(1, chunk_size - overlap)
    for start in range(0, len(words), step):
        window = words[start : start + chunk_size]
        if not window:
            break
        chunks.append(" ".join(window))
        if start + chunk_size >= len(words):
            break
    return chunks


async def ingest_text(text: str, source: str) -> int:
    """Chunk, embed, and store text. Returns number of chunks added."""
    chunks = chunk_text(text, settings.chunk_size, settings.chunk_overlap)
    if not chunks:
        return 0

    collection = store.get_collection()

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []
    embeddings: list[list[float]] = []

    for index, chunk in enumerate(chunks):
        # Sequential embedding keeps VRAM pressure low on a 6GB GPU.
        vector = await ollama_client.embed(chunk)
        ids.append(f"{source}::{index}")
        documents.append(chunk)
        metadatas.append({"source": source, "chunk_index": index})
        embeddings.append(vector)

    collection.upsert(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
        embeddings=embeddings,
    )
    return len(chunks)
