#!/usr/bin/env bash
# Per-boot startup for the Local RAG Server on Cloud Agents.
# Brings up the Ollama daemon (CPU backend) and returns once it is reachable.
# The FastAPI app and dashboard run as separate visible terminals.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

OLLAMA_BIN="$(command -v ollama || echo /usr/local/bin/ollama)"

if curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "==> Ollama already running"
else
  echo "==> Starting Ollama (CPU backend)"
  nohup "$OLLAMA_BIN" serve >/tmp/ollama.log 2>&1 &
  for _ in $(seq 1 60); do
    curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1 && break
    sleep 1
  done
fi

if curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "==> Ollama is up: $(curl -fsS http://localhost:11434/api/tags)"
else
  echo "!! Ollama did not become reachable on http://localhost:11434" >&2
  exit 1
fi
