# AI Employee Platform

## Tech Stack
- **Backend**: Python 3.12 + FastAPI + SQLAlchemy (async) + PostgreSQL
- **Frontend**: Next.js 14 + TypeScript + Tailwind CSS + Radix UI primitives + Framer Motion
- **Agent Runtime**: Claude Code CLI (headless, `-p` + `--output-format stream-json`)
- **Infrastructure**: Docker, Redis (PubSub + Queue), Docker SDK for Python
- **Telegram**: python-telegram-bot v21

## Architecture
- `orchestrator/` - FastAPI backend, manages Docker containers and routes tasks
- `agent/` - Docker container image, runs Claude Code CLI per task
- `frontend/` - Next.js Web UI (dark theme, glassmorphism style)
- Communication: Redis PubSub (logs), Redis Queue (tasks), WebSocket (UI)

## Frontend Rules (IMPORTANT)
- **NO shadcn/ui** - this project does NOT use shadcn/ui. Never import from `@/components/ui/*`
- Use **plain Tailwind CSS** classes for all styling
- Use **@radix-ui/react-dialog**, **@radix-ui/react-dropdown-menu**, etc. for primitives
- Use **framer-motion** for animations (stagger, scale, opacity transitions)
- Use **lucide-react** for icons
- Use the `cn()` utility from `@/lib/utils` for conditional classes
- Cards: `rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5`
- Buttons primary: `rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20`
- Buttons secondary: `rounded-xl px-4 py-2 text-sm text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04]`
- Badges: `inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-medium` with color variants like `bg-emerald-500/10 text-emerald-400 border-emerald-500/20`
- Modals: Use `@radix-ui/react-dialog` with `framer-motion` animations (see `create-agent-modal.tsx` as reference)
- Labels: `text-[11px] font-medium text-muted-foreground/70`
- Inputs: `rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm`
- Always check existing components for patterns before writing new UI code
- Always run `npm run build` (or `next build`) to verify no import errors before committing

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

## Learned Patterns
- Audit logging: model + alembic migration + API router + register in router.py + export from models/__init__.py
- Health checks: use `request.app.state.redis` and `request.app.state.docker` to access services; return 503 on critical failure
- Monitoring stack: use docker-compose.monitoring.yml overlay; prometheus scrapes orchestrator:8000/metrics, postgres-exporter, redis-exporter, node-exporter, cadvisor
- Production infra already uses Traefik (docker-compose.prod.yml) — nginx TODOs are covered by Traefik config in traefik/traefik.yml
- Backup scripts: pg_dump via `docker exec`, volume backup via `docker run alpine tar czf`, SHA256 manifest for integrity; systemd timer preferred over cron
