# Local RAG Server

A self-hosted Retrieval-Augmented Generation server running entirely on this machine.
It uses [Ollama](https://ollama.com) for GPU-accelerated LLM and embedding inference, a
[FastAPI](https://fastapi.tiangolo.com) application for the RAG pipeline, and
[ChromaDB](https://www.trychroma.com) as a local, file-based vector store.

For day-to-day usage (ingesting documents, asking questions, interpreting
answers), see the [RAG User Guide](docs/RAG_USER_GUIDE.md). For the trading
knowledge corpus (acquisition, batch ingest, validation), see the
[Corpus Runbook](docs/CORPUS_RUNBOOK.md). For a conceptual plan to build
agentic trading on top of this stack, see
[Agentic Trading Roadmap](docs/AGENTIC_TRADING_ROADMAP.md).

## Architecture

```
client -> FastAPI (/ingest, /query, /health)
            |-> chunk + embed (Ollama: nomic-embed-text)
            |-> store / retrieve (ChromaDB)
            \-> generate answer (Ollama: llama3.2:3b)
```

- Ollama serves the chat LLM and the embedding model on `http://localhost:11434`.
- FastAPI handles ingestion (chunking, embedding, storage) and querying
  (embed question -> retrieve top-k -> prompt -> generate).
- ChromaDB persists vectors to `./chroma_db`.

## Hardware

| Component | Specification |
|-----------|---------------|
| **Host** | Dell OptiPlex 9010 |
| **CPU** | Intel Core i7-3770 @ 3.40 GHz (4 cores / 8 threads) |
| **RAM** | 16 GB |
| **GPU** | NVIDIA GeForce RTX 3050 6 GB (GA107) |
| **Driver** | NVIDIA 595.71.05 (open kernel module) |
| **OS** | Ubuntu 24.04, kernel 6.14.0-37-generic |
| **OS disk** | Samsung SSD 870 232.9 GB (`/`) |
| **Data disk** | WDC WDS100T2B0A 931.5 GB (`/media/maxim/Store`) |

The 6 GB VRAM ceiling is why 3B-class models are used. All 29 layers of
`llama3.2:3b` fit in VRAM (~2.7 GB resident).

## GPU backend

This machine runs Ollama on the **CUDA** backend (driver 595). The previous
deployment used a GTX 1660 SUPER with driver 535, which was too old for
Ollama's CUDA kernels and required a Vulkan fallback (`CUDA_VISIBLE_DEVICES=-1`).

If CUDA breaks after an Ollama or driver upgrade, fall back to Vulkan by
uncommenting these lines in `scripts/start.sh`:

```bash
export CUDA_VISIBLE_DEVICES="-1"
export OLLAMA_VULKAN=1
```

## Install

On this machine:

- Ollama installed system-wide at `/usr/local/bin/ollama` (via the official installer)
- Python venv at `./.venv` (pip bootstrapped via `get-pip.py`)
- Models in `~/.ollama/models`: `llama3.2:3b`, `nomic-embed-text`

To reproduce on another machine:

```bash
# Ollama (system install — recommended when root is available)
curl -fsSL https://ollama.com/install.sh | sh

# Or user-space install:
# curl -fsSL https://github.com/ollama/ollama/releases/latest/download/ollama-linux-amd64.tar.zst -o /tmp/ollama.tar.zst
# mkdir -p ~/.local && tar --zstd -C ~/.local -xf /tmp/ollama.tar.zst

# Python env
cd ~/Projects/sandbox002
python3 -m venv --without-pip .venv
curl -fsSL https://bootstrap.pypa.io/get-pip.py | .venv/bin/python
.venv/bin/pip install -r requirements.txt
cp .env.example .env

# Models
ollama serve &
ollama pull llama3.2:3b
ollama pull nomic-embed-text
```

## Run

```bash
bash scripts/start.sh
```

This starts Ollama (CUDA GPU backend) if needed, then the FastAPI server.
Interactive API docs: http://localhost:8000/docs

## Usage

Ingest raw text:

```bash
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{"text": "Your document text here.", "source": "notes"}'
```

Ingest a file (.txt, .md, .pdf, .epub):

```bash
curl -X POST http://localhost:8000/ingest/file \
  -F "file=@/path/to/document.pdf" \
  -F "source=my-doc"
```

Query:

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What does the document say about X?"}'
```

Health:

```bash
curl http://localhost:8000/health
```

## Configuration

All settings are read from `.env` (see `.env.example`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API endpoint |
| `OLLAMA_LLM_MODEL` | `llama3.2:3b` | Chat model |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | Embedding model |
| `CHROMA_PERSIST_DIR` | `./chroma_db` | Vector store location |
| `CHUNK_SIZE` | `500` | Chunk size in tokens (approx) |
| `CHUNK_OVERLAP` | `50` | Overlap between chunks |
| `TOP_K` | `5` | Chunks retrieved per query |
| `PDF_OCR_ENABLED` | `true` | OCR image-only PDF pages when no text layer is present |
| `PDF_OCR_DPI` | `200` | Render resolution for OCR (higher = slower, more accurate) |
| `PDF_FIGURES_ENABLED` | `true` | Extract and caption charts/figures from PDFs |
| `PDF_FIGURE_TEXT_THRESHOLD` | `200` | OCR char count below which a scanned page becomes a figure candidate |
| `PDF_FIGURE_MIN_SIZE` | `150` | Min width/height for embedded images |
| `FIGURES_DIR` | `./data/figures` | Stored figure PNG paths |
| `OLLAMA_VISION_MODEL` | `moondream` | Vision model for figure captions at ingest |

Scanned PDFs require `tesseract-ocr`; figure captions require a vision model:

```bash
sudo apt install tesseract-ocr
ollama pull moondream
```

Large scanned books (hundreds of pages) can take hours to ingest (OCR + selective
vision). Ingest is resumable: existing figure IDs are skipped on re-run.

Test figure extraction on the Murphy PDF (first 5 pages):

```bash
bash scripts/test_pdf_figures.sh
```

## Project Layout

```
app/
  main.py           FastAPI app + routes
  config.py         env-based settings
  ollama_client.py  LLM + embedding calls
  ingest.py         chunk, embed, store
  pdf_ingest.py     unified PDF text + figure extraction
  pdf_figures.py    figure asset extraction
  rag.py            retrieve + prompt + generate
  store.py          ChromaDB client
  models.py         Pydantic schemas
data/documents/     drop zone for source files
data/figures/       extracted figure images (gitignored)
chroma_db/          persisted vector store
scripts/            system setup helpers
```
