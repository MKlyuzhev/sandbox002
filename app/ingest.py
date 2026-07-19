import logging
import re
from pathlib import Path

from . import ollama_client, store
from .config import settings
from .metadata import build_chunk_metadata, prefix_chunk_text, validate_metadata
from .pdf_figures import FigureAsset

logger = logging.getLogger(__name__)

# Dense PDF text can exceed nomic-embed-text token limits well before 6k chars.
MAX_CHUNK_CHARS = 1800


def split_oversized_chunks(chunks: list[str], max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split word chunks that exceed the embedding model context limit."""
    result: list[str] = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            result.append(chunk)
            continue

        words = chunk.split()
        current: list[str] = []
        current_len = 0
        for word in words:
            extra = len(word) + (1 if current else 0)
            if current and current_len + extra > max_chars:
                result.append(" ".join(current))
                current = [word]
                current_len = len(word)
            else:
                current.append(word)
                current_len += extra
        if current:
            result.append(" ".join(current))
    return result


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


async def ingest_text(
    text: str,
    source: str,
    metadata: dict[str, str] | None = None,
) -> int:
    """Chunk, embed, and store text. Returns number of chunks added."""
    doc_metadata = validate_metadata(metadata)
    chunks = chunk_text(text, settings.chunk_size, settings.chunk_overlap)
    chunks = split_oversized_chunks(chunks)
    if not chunks:
        return 0

    collection = store.get_collection()

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []
    embeddings: list[list[float]] = []

    for index, chunk in enumerate(chunks):
        # Sequential embedding keeps VRAM pressure low on a 6GB GPU.
        document = prefix_chunk_text(chunk, source, doc_metadata)
        vector = await ollama_client.embed(document)
        ids.append(f"{source}::{index}")
        documents.append(document)
        metadatas.append(
            build_chunk_metadata(
                source,
                "text",
                index,
                doc_metadata,
            )
        )
        embeddings.append(vector)

    collection.upsert(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
        embeddings=embeddings,
    )
    return len(chunks)


async def ingest_figures(
    figures: list[FigureAsset],
    source: str,
    metadata: dict[str, str] | None = None,
) -> int:
    """Caption, embed, and store figure assets. Returns figures added (skips existing)."""
    if not figures:
        return 0

    doc_metadata = validate_metadata(metadata)
    collection = store.get_collection()
    added = 0

    for chunk_index, figure in enumerate(figures):
        fig_id = _figure_id(source, figure.page, figure.figure_index)
        existing = collection.get(ids=[fig_id], include=[])
        if existing.get("ids"):
            logger.info("Skipping existing figure %s", fig_id)
            continue

        try:
            caption = await ollama_client.describe_image(str(figure.image_path))
        except ollama_client.OllamaError as exc:
            logger.warning("Skipping figure %s: %s", fig_id, exc)
            continue

        document = _figure_document(figure.page, caption, figure.ocr_snippet)
        document = prefix_chunk_text(document, source, doc_metadata)
        vector = await ollama_client.embed(document)
        image_path = _relative_path(figure.image_path)

        collection.upsert(
            ids=[fig_id],
            documents=[document],
            metadatas=[
                build_chunk_metadata(
                    source,
                    "figure",
                    chunk_index,
                    doc_metadata,
                    page=figure.page,
                    figure_index=figure.figure_index,
                    image_path=image_path,
                )
            ],
            embeddings=[vector],
        )
        added += 1
        logger.info("Ingested figure %s", fig_id)

    return added
