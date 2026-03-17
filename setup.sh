#!/bin/bash
# ============================================================
# Setup Script — Install all dependencies for Earnings App
# Run this once on a fresh VM after cloning the repo.
# ============================================================

set -e

echo "=== Installing system packages ==="
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    wkhtmltopdf \
    poppler-utils \
    ffmpeg \
    xvfb \
    libgl1-mesa-glx \
    libgl1-mesa-dri \
    libegl1-mesa \
    libgles2-mesa \
    libglvnd0 \
    libglx0 \
    libopengl0 \
    libxkbcommon0 \
    libxcb-xfixes0 \
    libxcb-shape0 \
    libxcb-render0 \
    libxcb-shm0 \
    libxcb-icccm4 \
    libxcb-image0 \
    libxcb-keysyms1 \
    libxcb-randr0 \
    libxcb-render-util0 \
    libxcb-xinerama0 \
    curl \
    ca-certificates

echo ""
echo "=== Installing Node.js 20.x ==="
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash -
sudo apt-get install -y nodejs

echo ""
echo "=== Installing Claude CLI ==="
sudo npm install -g @anthropic-ai/claude-code

echo ""
echo "=== Installing Python dependencies ==="
pip install -r requirements.txt

echo ""
echo "=== Creating reports directory ==="
mkdir -p reports

echo ""
echo "=== Setup complete ==="
echo "Copy .env.example to .env and set your ANTHROPIC_API_KEY before running the app."
