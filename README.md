# Local RAG Server

A self-hosted Retrieval-Augmented Generation server running entirely on this machine.
It uses [Ollama](https://ollama.com) for GPU-accelerated LLM and embedding inference, a
[FastAPI](https://fastapi.tiangolo.com) application for the RAG pipeline, and
[ChromaDB](https://www.trychroma.com) as a local, file-based vector store.

For day-to-day usage (ingesting documents, asking questions, interpreting
answers), see the [RAG User Guide](docs/RAG_USER_GUIDE.md).

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

- GPU: NVIDIA GeForce GTX 1660 SUPER (6 GB VRAM), driver 535 / CUDA 12.2
- CPU: Intel i7-2700K (4c/8t), 16 GB RAM

The 6 GB VRAM ceiling is why 3B-class models are used. All 29 layers of
`llama3.2:3b` fit in VRAM (~2.7 GB resident).

## GPU backend (important)

This machine runs Ollama on the **Vulkan** backend, not CUDA. Driver 535 ships
CUDA 12.2, which is too old for the current Ollama build's CUDA kernels and
fails with `CUDA error: device kernel image is invalid`. Vulkan offloads all
model layers to the GPU and works well, so the startup script forces it via
`CUDA_VISIBLE_DEVICES=-1`.

To switch to the CUDA backend later, upgrade the NVIDIA driver to 580+
(`sudo ubuntu-drivers install nvidia:580 && sudo reboot`) and remove the
`CUDA_VISIBLE_DEVICES=-1` line from `scripts/start.sh`.

## Install (already done on this machine)

This deployment was installed entirely in user space (no root required):

- Python venv at `./.venv` (pip bootstrapped via `get-pip.py`)
- Ollama extracted to `~/.local` (binary at `~/.local/bin/ollama`)
- Models in `~/.ollama/models`: `llama3.2:3b`, `nomic-embed-text`

To reproduce on another machine:

```bash
# Ollama (user-space)
curl -fsSL https://github.com/ollama/ollama/releases/latest/download/ollama-linux-amd64.tar.zst -o /tmp/ollama.tar.zst
mkdir -p ~/.local && tar --zstd -C ~/.local -xf /tmp/ollama.tar.zst

# Python env
cd ~/Projects/sandbox002
python3 -m venv --without-pip .venv
curl -fsSL https://bootstrap.pypa.io/get-pip.py | .venv/bin/python
.venv/bin/pip install -r requirements.txt
cp .env.example .env

# Models
CUDA_VISIBLE_DEVICES=-1 ~/.local/bin/ollama serve &
~/.local/bin/ollama pull llama3.2:3b
~/.local/bin/ollama pull nomic-embed-text
```

## Run

```bash
bash scripts/start.sh
```

This starts Ollama (Vulkan GPU backend) if needed, then the FastAPI server.
Interactive API docs: http://localhost:8000/docs

## Usage

Ingest raw text:

```bash
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{"text": "Your document text here.", "source": "notes"}'
```

Ingest a file (.txt, .md, .pdf):

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

## Project Layout

```
app/
  main.py           FastAPI app + routes
  config.py         env-based settings
  ollama_client.py  LLM + embedding calls
  ingest.py         chunk, embed, store
  rag.py            retrieve + prompt + generate
  store.py          ChromaDB client
  models.py         Pydantic schemas
data/documents/     drop zone for source files
chroma_db/          persisted vector store
scripts/            system setup helpers
```
