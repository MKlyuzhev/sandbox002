"""Extract plain text from EPUB bytes."""

from __future__ import annotations

import io
import logging

import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub

logger = logging.getLogger(__name__)


class EpubTextError(Exception):
    """Raised when EPUB text cannot be extracted."""


def _html_to_text(html: bytes) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)


def _chapter_text(item: epub.EpubItem) -> str:
    return _html_to_text(item.get_content())


def extract_text(raw: bytes) -> str:
    """Return full book text in spine reading order."""
    try:
        book = epub.read_epub(io.BytesIO(raw))
    except Exception as exc:
        raise EpubTextError(f"Could not read EPUB: {exc}") from exc

    parts: list[str] = []
    seen: set[str] = set()

    for item_id, _linear in book.spine:
        item = book.get_item_with_id(item_id)
        if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        if item.get_id() in seen:
            continue
        seen.add(item.get_id())
        text = _chapter_text(item)
        if text:
            parts.append(text)

    if not parts:
        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            if item.get_id() in seen:
                continue
            text = _chapter_text(item)
            if text:
                parts.append(text)

    combined = "\n\n".join(parts)
    if not combined.strip():
        raise EpubTextError("No extractable text in file")

    logger.info("Extracted text from %d EPUB sections", len(parts))
    return combined
