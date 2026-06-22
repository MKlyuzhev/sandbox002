#!/usr/bin/env bash
# System-level setup helper for the local RAG server.
#
# NOTE: This project was installed entirely in user space (no root) because
# passwordless sudo was unavailable:
#   - Python venv at ./.venv (pip bootstrapped via get-pip.py)
#   - Ollama extracted to ~/.local (binary at ~/.local/bin/ollama)
#   - Models stored in ~/.ollama/models
#
# The commands below are OPTIONAL improvements that require root. Run only the
# parts you need.
set -euo pipefail

# --- GPU: enable the CUDA backend (currently using Vulkan fallback) ----------
# Driver 535 ships CUDA 12.2, which is too old for this Ollama build's CUDA
# kernels (error: "device kernel image is invalid"). Ollama currently runs on
# the Vulkan backend with full GPU offload, so this is optional. To switch to
# CUDA, upgrade the driver, then remove CUDA_VISIBLE_DEVICES=-1 from start.sh:
#
#   sudo ubuntu-drivers install nvidia:580
#   sudo reboot

# --- Optional: install Ollama as a system service ----------------------------
# The official installer sets up a systemd unit (needs root):
#   curl -fsSL https://ollama.com/install.sh | sh
#   sudo systemctl enable --now ollama

# --- Optional: mount the secondary ext4 disk (sdb1) at /data -----------------
# Useful if the root partition fills up. Move OLLAMA_MODELS and chroma there.
#   sudo mkdir -p /data
#   echo 'UUID=be87564b-661a-47fe-a5bb-18f6c9a44b2d /data ext4 defaults 0 2' | sudo tee -a /etc/fstab
#   sudo mount /data
#   sudo chown -R "$USER:$USER" /data
#   mkdir -p /data/ollama/models /data/rag/chroma
#   # then set OLLAMA_MODELS=/data/ollama/models and CHROMA_PERSIST_DIR=/data/rag/chroma

echo "This file documents optional root-level setup. See comments above."
