#!/bin/bash
set -e

echo "=== AI Employee Platform Setup ==="
echo ""

# Check prerequisites
if ! command -v docker &> /dev/null; then
    echo "ERROR: Docker is not installed. Please install Docker Desktop."
    exit 1
fi

if ! docker info &> /dev/null 2>&1; then
    echo "ERROR: Docker is not running. Please start Docker Desktop."
    exit 1
fi

echo "[OK] Docker is running"

# Create .env if not exists
if [ ! -f .env ]; then
    cp .env.example .env
    echo "[INFO] Created .env from .env.example"
    echo ""
    echo "IMPORTANT: Edit .env and add your CLAUDE_CODE_OAUTH_TOKEN"
    echo "  1. Open macOS Keychain Access"
    echo "  2. Search for 'claude'"
    echo "  3. Copy the token"
    echo "  4. Paste it into .env as CLAUDE_CODE_OAUTH_TOKEN=<token>"
    echo ""
else
    echo "[OK] .env exists"
fi

# Check if OAuth token is set
if grep -q "CLAUDE_CODE_OAUTH_TOKEN=$" .env 2>/dev/null; then
    echo "WARNING: CLAUDE_CODE_OAUTH_TOKEN is empty in .env"
    echo "Agents will not work without it!"
    echo ""
fi

# Build agent image
echo "Building agent Docker image..."
docker build -t ai-employee-agent:latest ./agent

echo ""
echo "[OK] Agent image built"

# Start infrastructure
echo "Starting infrastructure (PostgreSQL + Redis)..."
docker compose up -d postgres redis

# Wait for healthy services
echo "Waiting for services to be healthy..."
sleep 5

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Make sure CLAUDE_CODE_OAUTH_TOKEN is set in .env"
echo "  2. Run: docker compose up"
echo "  3. Open: http://localhost:3000 (Web UI)"
echo "  4. API: http://localhost:8000/docs (FastAPI Swagger)"
