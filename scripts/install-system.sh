#!/usr/bin/env bash
# System-level setup helper for the local RAG server.
#
# Current deployment (Dell OptiPlex 9010):
#   - Ollama system install at /usr/local/bin/ollama
#   - Python venv at ./.venv
#   - Models stored in ~/.ollama/models
#   - NVIDIA driver 595 (CUDA backend); secondary disk at /media/maxim/Store
#
# The commands below are OPTIONAL improvements that require root. Run only the
# parts you need.
set -euo pipefail

# --- GPU: CUDA backend (driver 595) ------------------------------------------
# Ollama uses the CUDA backend on this machine. If a future Ollama or driver
# upgrade breaks CUDA, fall back to Vulkan by uncommenting the exports in
# scripts/start.sh (CUDA_VISIBLE_DEVICES=-1, OLLAMA_VULKAN=1).
#
# To reinstall or upgrade the driver:
#   sudo ubuntu-drivers install nvidia:595-open
#   sudo reboot

# --- OCR for scanned PDFs (required for image-only documents) ---------------
#   sudo apt install tesseract-ocr
# Toggle OCR or adjust render quality in .env: PDF_OCR_ENABLED, PDF_OCR_DPI

# --- Vision model for PDF figure captions ------------------------------------
#   ollama pull moondream
# Configure in .env: OLLAMA_VISION_MODEL, PDF_FIGURES_ENABLED, FIGURES_DIR

# --- Optional: install Ollama as a system service ----------------------------
# The official installer sets up a systemd unit (needs root):
#   curl -fsSL https://ollama.com/install.sh | sh
#   sudo systemctl enable --now ollama

# --- Optional: mount the secondary ext4 disk (sdb1) at /data -----------------
# Currently auto-mounted at /media/maxim/Store. To use a fixed path instead
# (useful if the root partition fills up), move OLLAMA_MODELS and chroma there:
#   sudo mkdir -p /data
#   sudo blkid /dev/sdb1   # copy the UUID
#   echo 'UUID=<uuid> /data ext4 defaults 0 2' | sudo tee -a /etc/fstab
#   sudo mount /data
#   sudo chown -R "$USER:$USER" /data
#   mkdir -p /data/ollama/models /data/rag/chroma
#   # then set OLLAMA_MODELS=/data/ollama/models and CHROMA_PERSIST_DIR=/data/rag/chroma

echo "This file documents optional root-level setup. See comments above."
