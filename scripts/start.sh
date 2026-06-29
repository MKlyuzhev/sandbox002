#!/usr/bin/env bash
# Start the local RAG server stack: Ollama (CUDA GPU backend) + FastAPI.
# Usage: bash scripts/start.sh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -x /usr/local/bin/ollama ]]; then
  OLLAMA_BIN=/usr/local/bin/ollama
elif [[ -x "$HOME/.local/bin/ollama" ]]; then
  OLLAMA_BIN="$HOME/.local/bin/ollama"
else
  OLLAMA_BIN="$(command -v ollama)"
fi

# Uses the CUDA backend (driver 595 / CUDA 13.2). If a future Ollama or driver
# change breaks CUDA, fall back to the Vulkan backend by uncommenting:
#   export CUDA_VISIBLE_DEVICES="-1"
#   export OLLAMA_VULKAN=1

if ! curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "==> Starting Ollama (CUDA GPU backend)..."
  nohup "$OLLAMA_BIN" serve > /tmp/ollama.log 2>&1 &
  for _ in $(seq 1 30); do
    curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1 && break
    sleep 1
  done
fi
echo "==> Ollama is up: $(curl -fsS http://localhost:11434/api/tags)"

echo "==> Starting RAG API on http://0.0.0.0:8000 ..."
cd "$PROJECT_DIR"
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
