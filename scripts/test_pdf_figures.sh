#!/usr/bin/env bash
# Smoke test for PDF figure extraction and vision captions.
# Usage: bash scripts/test_pdf_figures.sh [path] [max_pages]
set -euo pipefail

PDF="${1:-$HOME/Downloads/John_J._Murphy_-_Technical_Analysis_Of_The_Financial_Markets.pdf}"
MAX_PAGES="${2:-5}"

if ! command -v tesseract >/dev/null; then
  echo "Install tesseract first: sudo apt install tesseract-ocr"
  exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if ! curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "Start Ollama first: ollama serve"
  exit 1
fi

.venv/bin/python - <<PY
import asyncio
from pathlib import Path

from app.config import settings
from app.pdf_ingest import process_pdf
from app import ollama_client

pdf = Path("$PDF")
max_pages = int("$MAX_PAGES")
source = pdf.stem
raw = pdf.read_bytes()

result = process_pdf(raw, source, max_pages=max_pages)
print(f"Pages processed: {max_pages}")
print(f"Text length: {len(result.full_text)} chars")
print(f"Figures extracted: {len(result.figures)}")
print("---")

async def caption_samples():
    for figure in result.figures[:3]:
        print(f"Figure page {figure.page} index {figure.figure_index}: {figure.image_path}")
        try:
            caption = await ollama_client.describe_image(str(figure.image_path))
            print(caption[:400])
        except Exception as exc:
            print(f"Caption failed: {exc}")
        print("---")

asyncio.run(caption_samples())
PY
