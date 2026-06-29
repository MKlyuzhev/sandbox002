"""Unified PDF processing: text extraction, OCR, and figure assets."""

from __future__ import annotations

import io
import logging
import shutil
from dataclasses import dataclass

import fitz
import pytesseract
from PIL import Image
from pypdf import PdfReader

from .config import settings
from .pdf_figures import FigureAsset, extract_page_figures, slugify_source

logger = logging.getLogger(__name__)


class PdfIngestError(Exception):
    """Raised when PDF processing fails."""


@dataclass
class PdfIngestResult:
    full_text: str
    figures: list[FigureAsset]


def ocr_page(page: fitz.Page) -> str:
    """OCR a single PDF page rendered at the configured DPI."""
    pix = page.get_pixmap(dpi=settings.pdf_ocr_dpi)
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return pytesseract.image_to_string(image)


def process_pdf(raw: bytes, source: str, max_pages: int | None = None) -> PdfIngestResult:
    """Extract text and figure assets from a PDF in a single pass."""
    try:
        reader = PdfReader(io.BytesIO(raw))
    except Exception as exc:
        raise PdfIngestError(f"Could not read PDF: {exc}") from exc

    total_pages = len(reader.pages)
    if total_pages == 0:
        raise PdfIngestError("PDF has no pages")

    page_count = min(total_pages, max_pages) if max_pages is not None else total_pages

    doc = fitz.open(stream=raw, filetype="pdf")
    if doc.page_count != total_pages:
        doc.close()
        raise PdfIngestError("PDF page count mismatch between parsers")

    source_slug = slugify_source(source)
    text_parts: list[str] = []
    figures: list[FigureAsset] = []
    ocr_pages = 0

    for index in range(page_count):
        page_num = index + 1
        page = doc[index]
        embedded = (reader.pages[index].extract_text() or "").strip()
        ocr_used = False
        page_text = embedded

        if not page_text:
            if not settings.pdf_ocr_enabled:
                page_text = ""
            elif not shutil.which("tesseract"):
                doc.close()
                raise PdfIngestError(
                    "PDF has image-only pages but tesseract is not installed. "
                    "Install with: sudo apt install tesseract-ocr"
                )
            else:
                ocr_pages += 1
                if ocr_pages == 1 or ocr_pages % 25 == 0 or page_num == page_count:
                    logger.info("OCR page %d/%d", page_num, page_count)
                page_text = ocr_page(page).strip()
                ocr_used = True

        if page_text:
            text_parts.append(page_text)

        page_figures = extract_page_figures(
            page, doc, page_num, source_slug, page_text, ocr_used
        )
        figures.extend(page_figures)

    doc.close()

    if ocr_pages:
        logger.info("OCR applied to %d of %d pages", ocr_pages, page_count)
    if figures:
        logger.info("Extracted %d figures from PDF", len(figures))

    combined = "\n\n".join(text_parts)
    if not combined.strip() and not figures:
        raise PdfIngestError("No extractable text or figures in file")

    return PdfIngestResult(full_text=combined, figures=figures)
