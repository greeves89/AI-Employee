#!/bin/bash
# AI-Employee TTS Service — native Mac installer
# Runs with Apple Metal (MPS) GPU — M4 Pro optimized

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== AI-Employee TTS Service — Mac Setup ==="

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Install via: brew install python"
    exit 1
fi

# Create venv
VENV="$SCRIPT_DIR/.venv"
if [ ! -d "$VENV" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV"
fi

source "$VENV/bin/activate"

echo "Installing dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r "$SCRIPT_DIR/requirements_mac.txt"

echo ""
echo "✅ Installation complete."
echo ""
echo "Start the service:  $SCRIPT_DIR/start_mac.sh"
echo "Auto-start on boot: $SCRIPT_DIR/install_launchd.sh"
