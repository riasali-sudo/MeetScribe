#!/usr/bin/env bash
set -euo pipefail

echo "=== MeetScribe Setup ==="

# Detect OS
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    echo "Installing system dependencies..."
    sudo apt-get update -qq
    sudo apt-get install -y --no-install-recommends \
        xvfb \
        pulseaudio \
        pulseaudio-utils \
        ffmpeg \
        fonts-liberation \
        libnss3 \
        libatk-bridge2.0-0 \
        libdrm2 \
        libxcomposite1 \
        libxdamage1 \
        libxrandr2 \
        libgbm1 \
        libasound2
elif [[ "$OSTYPE" == "darwin"* ]]; then
    echo "macOS detected. Install dependencies via Homebrew:"
    echo "  brew install ffmpeg"
    echo "Note: Xvfb and PulseAudio are Linux-only. Use Docker for full headless support on macOS."
fi

# Python dependencies
echo "Installing Python dependencies..."
pip install -r requirements.txt

# Playwright browsers (Chromium only)
echo "Installing Playwright Chromium..."
python -m playwright install chromium
python -m playwright install-deps chromium 2>/dev/null || true

# Create directories
mkdir -p recordings transcripts data

echo "=== Setup Complete ==="
echo "Run with: python -m api.main"
