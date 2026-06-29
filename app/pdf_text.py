"""Extract text from PDF bytes, with OCR fallback for image-only pages."""

from __future__ import annotations

from .pdf_ingest import PdfIngestError, process_pdf


class PdfTextError(Exception):
    """Raised when PDF text cannot be extracted."""


def extract_text(raw: bytes) -> str:
    """Return full document text, OCRing pages that have no embedded text layer."""
    try:
        result = process_pdf(raw, "document")
    except PdfIngestError as exc:
        raise PdfTextError(str(exc)) from exc

    if not result.full_text.strip():
        raise PdfTextError("No extractable text in file (OCR produced no text)")

    return result.full_text
