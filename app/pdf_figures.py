"""Extract figure assets from PDF pages."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import fitz

from .config import settings

logger = logging.getLogger(__name__)


@dataclass
class FigureAsset:
    page: int
    figure_index: int
    image_path: Path
    ocr_snippet: str


def slugify_source(source: str) -> str:
    slug = re.sub(r"[^\w\-]+", "_", source.lower()).strip("_")
    return slug or "document"


def _figures_dir(source_slug: str) -> Path:
    path = Path(settings.figures_dir) / source_slug
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save_pixmap(pix: fitz.Pixmap, dest: Path) -> None:
    if pix.n - pix.alpha > 3:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    pix.save(str(dest))


def _extract_embedded_images(
    page: fitz.Page,
    doc: fitz.Document,
    page_num: int,
    source_slug: str,
    ocr_snippet: str,
) -> list[FigureAsset]:
    min_size = settings.pdf_figure_min_size
    out_dir = _figures_dir(source_slug)
    assets: list[FigureAsset] = []

    for figure_index, img in enumerate(page.get_images(full=True)):
        xref = img[0]
        try:
            extracted = doc.extract_image(xref)
        except Exception:
            logger.debug("Skipping unreadable image xref %s on page %d", xref, page_num)
            continue

        width = extracted.get("width", 0)
        height = extracted.get("height", 0)
        if width < min_size or height < min_size:
            continue

        image_bytes = extracted["image"]
        ext = extracted.get("ext", "png")
        dest = out_dir / f"p{page_num:04d}_fig{figure_index:02d}.{ext}"
        dest.write_bytes(image_bytes)

        assets.append(
            FigureAsset(
                page=page_num,
                figure_index=figure_index,
                image_path=dest,
                ocr_snippet=ocr_snippet,
            )
        )

    return assets


def _page_render_figure(
    page: fitz.Page,
    page_num: int,
    source_slug: str,
    figure_index: int,
    ocr_snippet: str,
) -> FigureAsset:
    out_dir = _figures_dir(source_slug)
    dest = out_dir / f"p{page_num:04d}_fig{figure_index:02d}.png"
    pix = page.get_pixmap(dpi=settings.pdf_ocr_dpi)
    _save_pixmap(pix, dest)
    return FigureAsset(
        page=page_num,
        figure_index=figure_index,
        image_path=dest,
        ocr_snippet=ocr_snippet,
    )


def extract_page_figures(
    page: fitz.Page,
    doc: fitz.Document,
    page_num: int,
    source_slug: str,
    page_text: str,
    ocr_used: bool,
) -> list[FigureAsset]:
    """Extract figures from a single page."""
    if not settings.pdf_figures_enabled:
        return []

    ocr_snippet = page_text[:500] if page_text else ""
    assets = _extract_embedded_images(page, doc, page_num, source_slug, ocr_snippet)

    if assets:
        return assets

    if (
        ocr_used
        and len(page_text) < settings.pdf_figure_text_threshold
    ):
        logger.info(
            "Page %d: heuristic page render (OCR text %d chars)",
            page_num,
            len(page_text),
        )
        return [
            _page_render_figure(page, page_num, source_slug, 0, ocr_snippet),
        ]

    return []
