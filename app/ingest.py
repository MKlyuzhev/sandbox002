import logging
import re
from pathlib import Path

from . import ollama_client, store
from .config import settings
from .pdf_figures import FigureAsset

logger = logging.getLogger(__name__)


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


def _relative_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def _figure_id(source: str, page: int, figure_index: int) -> str:
    return f"{source}::fig::p{page:04d}::{figure_index}"


def _figure_document(page: int, caption: str, ocr_snippet: str) -> str:
    doc = f"[Figure page {page}] {caption.strip()}"
    if ocr_snippet.strip():
        doc += f"\n\nNearby text: {ocr_snippet.strip()}"
    return doc


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
        metadatas.append(
            {
                "chunk_type": "text",
                "source": source,
                "chunk_index": index,
            }
        )
        embeddings.append(vector)

    collection.upsert(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
        embeddings=embeddings,
    )
    return len(chunks)


async def ingest_figures(figures: list[FigureAsset], source: str) -> int:
    """Caption, embed, and store figure assets. Returns figures added (skips existing)."""
    if not figures:
        return 0

    collection = store.get_collection()
    added = 0

    for chunk_index, figure in enumerate(figures):
        fig_id = _figure_id(source, figure.page, figure.figure_index)
        existing = collection.get(ids=[fig_id], include=[])
        if existing.get("ids"):
            logger.info("Skipping existing figure %s", fig_id)
            continue

        caption = await ollama_client.describe_image(str(figure.image_path))
        document = _figure_document(figure.page, caption, figure.ocr_snippet)
        vector = await ollama_client.embed(document)
        image_path = _relative_path(figure.image_path)

        collection.upsert(
            ids=[fig_id],
            documents=[document],
            metadatas=[
                {
                    "chunk_type": "figure",
                    "source": source,
                    "chunk_index": chunk_index,
                    "page": figure.page,
                    "figure_index": figure.figure_index,
                    "image_path": image_path,
                }
            ],
            embeddings=[vector],
        )
        added += 1
        logger.info("Ingested figure %s", fig_id)

    return added
