#!/usr/bin/env bash
# Setup Playwright browsers for ticketmaster-bot
set -euo pipefail

echo "=== Ticketmaster Bot - Playwright Setup ==="

# Check Python
if ! command -v python3 &>/dev/null; then
  echo "Error: python3 not found"
  exit 1
fi

# Create venv if missing
if [ ! -d ".venv" ]; then
  echo "Creating virtualenv..."
  python3 -m venv .venv
fi

source .venv/bin/activate

echo "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "Installing Playwright Chromium..."
playwright install chromium
playwright install-deps chromium || true  # may need sudo on Linux

echo ""
echo "=== Setup complete ==="
echo "Activate with: source .venv/bin/activate"
echo "Then run: python run.py --dry-run"
