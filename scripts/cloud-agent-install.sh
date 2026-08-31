#!/usr/bin/env bash
# Idempotent Cloud Agent install for the Local RAG Server.
#
# Prepares everything an agent needs to run the stack end to end:
#   - system packages (tesseract-ocr for scanned-PDF OCR, zstd for the Ollama installer)
#   - Ollama (CPU backend; Cloud Agent VMs have no GPU)
#   - Python venv + pinned requirements
#   - .env (copied from .env.example on first run)
#   - Ollama models: the chat LLM and the embedding model
#
# Safe to run repeatedly: every step checks for existing state before doing work.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

echo "==> System packages (tesseract-ocr, zstd)"
if ! command -v tesseract >/dev/null 2>&1 || ! command -v zstd >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq tesseract-ocr zstd python3-venv python3-pip
fi

echo "==> Ollama"
if ! command -v ollama >/dev/null 2>&1 && [[ ! -x /usr/local/bin/ollama ]]; then
  curl -fsSL https://ollama.com/install.sh | sh
fi
OLLAMA_BIN="$(command -v ollama || echo /usr/local/bin/ollama)"

echo "==> Python venv + requirements"
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q

echo "==> .env"
if [[ ! -f .env ]]; then
  cp .env.example .env
fi

echo "==> Ollama models"
# A running server is required to pull. Start one only if none is reachable,
# pull any missing models, then stop the temporary server we started (the
# durable model files on disk are what matter and are captured by snapshots).
STARTED_OLLAMA=""
if ! curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1; then
  nohup "$OLLAMA_BIN" serve >/tmp/ollama-install.log 2>&1 &
  STARTED_OLLAMA="$!"
  for _ in $(seq 1 30); do
    curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1 && break
    sleep 1
  done
fi

# Read the model tags from .env so a customized config still pulls the right models.
LLM_MODEL="$(grep -E '^OLLAMA_LLM_MODEL=' .env | cut -d= -f2-)"
EMBED_MODEL="$(grep -E '^OLLAMA_EMBED_MODEL=' .env | cut -d= -f2-)"
LLM_MODEL="${LLM_MODEL:-llama3.2:3b}"
EMBED_MODEL="${EMBED_MODEL:-nomic-embed-text}"

INSTALLED_MODELS="$("$OLLAMA_BIN" list | awk 'NR>1 {print $1}')"
for model in "$LLM_MODEL" "$EMBED_MODEL"; do
  # `ollama list` reports untagged pulls as "<model>:latest"; match either form.
  if echo "$INSTALLED_MODELS" | grep -qxE "${model}(:latest)?"; then
    echo "    $model already present"
  else
    echo "    pulling $model ..."
    "$OLLAMA_BIN" pull "$model"
  fi
done

if [[ -n "$STARTED_OLLAMA" ]]; then
  kill "$STARTED_OLLAMA" >/dev/null 2>&1 || true
fi

echo "==> Install complete"
