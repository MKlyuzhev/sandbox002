"""Unified EPUB processing: text extraction and figure assets."""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urldefrag, urljoin

import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub
from PIL import Image

from .config import settings
from .epub_text import extract_text
from .pdf_figures import FigureAsset, slugify_source

logger = logging.getLogger(__name__)


class EpubIngestError(Exception):
    """Raised when EPUB processing fails."""


@dataclass
class EpubIngestResult:
    full_text: str
    figures: list[FigureAsset]


def _figures_dir(source_slug: str) -> Path:
    path = Path(settings.figures_dir) / source_slug
    path.mkdir(parents=True, exist_ok=True)
    return path


def _image_size(raw: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(raw)) as img:
        return img.size


def _save_image(raw: bytes, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)


def _normalize_href(href: str) -> str:
    href, _fragment = urldefrag(href)
    return unquote(href.lstrip("/"))


def _resolve_href(base_href: str, img_src: str) -> str:
    if img_src.startswith(("http://", "https://", "data:")):
        return ""
    joined = urljoin(base_href, img_src)
    return _normalize_href(joined)


def _item_by_href(book: epub.EpubBook, href: str) -> epub.EpubItem | None:
    normalized = _normalize_href(href)
    for item in book.get_items():
        item_name = _normalize_href(getattr(item, "get_name", lambda: item.get_id())())
        if item_name == normalized or item_name.endswith("/" + normalized):
            return item
    return None


def _chapter_index_for_item(book: epub.EpubBook, item: epub.EpubItem) -> int:
    for index, (item_id, _linear) in enumerate(book.spine):
        spine_item = book.get_item_with_id(item_id)
        if spine_item is not None and spine_item.get_id() == item.get_id():
            return index + 1
    return 0


def _extract_figures_from_images(
    book: epub.EpubBook,
    source_slug: str,
    seen_paths: set[str],
) -> list[FigureAsset]:
    if not settings.pdf_figures_enabled:
        return []

    min_size = settings.pdf_figure_min_size
    out_dir = _figures_dir(source_slug)
    assets: list[FigureAsset] = []
    figure_counter = 0

    for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
        href = _normalize_href(item.get_name())
        if href in seen_paths:
            continue
        seen_paths.add(href)

        raw = item.get_content()
        try:
            width, height = _image_size(raw)
        except Exception:
            logger.debug("Skipping unreadable EPUB image %s", href)
            continue

        if width < min_size or height < min_size:
            continue

        ext = Path(href).suffix.lstrip(".") or "png"
        chapter = _chapter_index_for_item(book, item)
        dest = out_dir / f"ch{chapter:04d}_fig{figure_counter:02d}.{ext}"
        _save_image(raw, dest)

        assets.append(
            FigureAsset(
                page=chapter,
                figure_index=figure_counter,
                image_path=dest,
                ocr_snippet="",
            )
        )
        figure_counter += 1

    return assets


def _extract_figures_from_html(
    book: epub.EpubBook,
    source_slug: str,
    seen_paths: set[str],
) -> list[FigureAsset]:
    if not settings.pdf_figures_enabled:
        return []

    min_size = settings.pdf_figure_min_size
    out_dir = _figures_dir(source_slug)
    assets: list[FigureAsset] = []
    figure_counter = 0

    for item_id, _linear in book.spine:
        item = book.get_item_with_id(item_id)
        if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue

        base_href = item.get_name()
        chapter = _chapter_index_for_item(book, item)
        soup = BeautifulSoup(item.get_content(), "html.parser")

        for img in soup.find_all("img"):
            src = (img.get("src") or "").strip()
            if not src:
                continue

            resolved = _resolve_href(base_href, src)
            if not resolved or resolved in seen_paths:
                continue

            image_item = _item_by_href(book, resolved)
            if image_item is None:
                continue

            seen_paths.add(resolved)
            raw = image_item.get_content()
            try:
                width, height = _image_size(raw)
            except Exception:
                logger.debug("Skipping unreadable EPUB image %s", resolved)
                continue

            if width < min_size or height < min_size:
                continue

            ext = Path(resolved).suffix.lstrip(".") or "png"
            dest = out_dir / f"ch{chapter:04d}_fig{figure_counter:02d}.{ext}"
            _save_image(raw, dest)

            nearby = img.find_parent(["p", "div", "figure", "section"])
            ocr_snippet = nearby.get_text(" ", strip=True)[:500] if nearby else ""

            assets.append(
                FigureAsset(
                    page=chapter,
                    figure_index=figure_counter,
                    image_path=dest,
                    ocr_snippet=ocr_snippet,
                )
            )
            figure_counter += 1

    return assets


def process_epub(raw: bytes, source: str) -> EpubIngestResult:
    """Extract text and figure assets from an EPUB."""
    try:
        book = epub.read_epub(io.BytesIO(raw))
    except Exception as exc:
        raise EpubIngestError(f"Could not read EPUB: {exc}") from exc

    full_text = extract_text(raw)
    source_slug = slugify_source(source)
    seen_paths: set[str] = set()

    figures = _extract_figures_from_html(book, source_slug, seen_paths)
    figures.extend(_extract_figures_from_images(book, source_slug, seen_paths))

    # Re-index figure_index sequentially after merge
    reindexed: list[FigureAsset] = []
    for index, figure in enumerate(figures):
        reindexed.append(
            FigureAsset(
                page=figure.page,
                figure_index=index,
                image_path=figure.image_path,
                ocr_snippet=figure.ocr_snippet,
            )
        )

    if reindexed:
        logger.info("Extracted %d figures from EPUB", len(reindexed))

    if not full_text.strip() and not reindexed:
        raise EpubIngestError("No extractable text or figures in file")

    return EpubIngestResult(full_text=full_text, figures=reindexed)
