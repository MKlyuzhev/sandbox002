#!/usr/bin/env bash
# Quick OCR smoke test for a PDF (defaults to Murphy technical analysis book).
# Usage: bash scripts/test_pdf_ocr.sh [path] [max_pages]
set -euo pipefail

PDF="${1:-$HOME/Downloads/John_J._Murphy_-_Technical_Analysis_Of_The_Financial_Markets.pdf}"
MAX_PAGES="${2:-3}"

if ! command -v tesseract >/dev/null; then
  echo "Install tesseract first: sudo apt install tesseract-ocr"
  exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

.venv/bin/python - <<PY
import io
import sys
from pathlib import Path

import fitz
import pytesseract
from PIL import Image
from pypdf import PdfReader

pdf = Path("$PDF")
max_pages = int("$MAX_PAGES")
raw = pdf.read_bytes()
reader = PdfReader(io.BytesIO(raw))
doc = fitz.open(stream=raw, filetype="pdf")

for i in range(min(max_pages, len(reader.pages))):
    embedded = (reader.pages[i].extract_text() or "").strip()
    if embedded:
        print(f"page {i + 1}: embedded text ({len(embedded)} chars)")
        print(embedded[:200])
        continue

    pix = doc[i].get_pixmap(dpi=200)
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    ocr = pytesseract.image_to_string(image).strip()
    print(f"page {i + 1}: OCR ({len(ocr)} chars)")
    print(ocr[:300] or "(empty)")
    print("---")
PY
