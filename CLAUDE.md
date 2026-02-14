# AI Employee Platform

## Tech Stack
- **Backend**: Python 3.12 + FastAPI + SQLAlchemy (async) + PostgreSQL
- **Frontend**: Next.js 14 + TypeScript + Tailwind CSS
- **Agent Runtime**: Claude Code CLI (headless, `-p` + `--output-format stream-json`)
- **Infrastructure**: Docker, Redis (PubSub + Queue), Docker SDK for Python
- **Telegram**: python-telegram-bot v21

## Architecture
- `orchestrator/` - FastAPI backend, manages Docker containers and routes tasks
- `agent/` - Docker container image, runs Claude Code CLI per task
- `frontend/` - Next.js Web UI
- Communication: Redis PubSub (logs), Redis Queue (tasks), WebSocket (UI)

## Key Commands
```bash
# Setup
./scripts/setup.sh

# Development
docker compose up --build

# Build agent image only
docker build -t ai-employee-agent:latest ./agent

# DB Migrations
cd orchestrator && alembic revision --autogenerate -m "description"
cd orchestrator && alembic upgrade head
```

## Critical Files
- `agent/app/agent_runner.py` - Claude CLI wrapper (heart of the system)
- `orchestrator/app/core/agent_manager.py` - Docker container lifecycle
- `orchestrator/app/core/load_balancer.py` - Task distribution
- `orchestrator/app/api/ws.py` - WebSocket log streaming
