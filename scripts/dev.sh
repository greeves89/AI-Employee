#!/bin/bash
set -e

echo "=== Starting AI Employee Development Environment ==="

# Build agent image if needed
if ! docker image inspect ai-employee-agent:latest &> /dev/null 2>&1; then
    echo "Building agent image..."
    docker build -t ai-employee-agent:latest ./agent
fi

# Start all services
docker compose up --build
