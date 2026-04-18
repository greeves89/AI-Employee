#!/bin/bash
# Start AI-Employee TTS Service natively on Mac (Metal GPU)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/.venv"

if [ ! -d "$VENV" ]; then
    echo "Run install_mac.sh first."
    exit 1
fi

source "$VENV/bin/activate"

export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"
export TTS_MODEL="${TTS_MODEL:-microsoft/VibeVoice-1.5B}"
export EDGE_TTS_VOICE="${EDGE_TTS_VOICE:-de-DE-ConradNeural}"

echo "Starting TTS Service on :8002 (model: $TTS_MODEL)"
cd "$SCRIPT_DIR"
exec uvicorn app.main:app --host 0.0.0.0 --port 8002
