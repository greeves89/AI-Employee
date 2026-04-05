# Contributing to AI-Employee

Thanks for thinking about contributing — whether you are fixing a typo, adding a new agent template, or shipping a major feature, we are happy you are here.

AI-Employee is a **Fair-Code** project. That means the code is open, contributions are welcome, and the community owns a real stake in the direction. It does not mean you are expected to work for free on something that somebody else will monetize — please read the [Contributor License Agreement](#contributor-license-agreement) section below.

## Ways to Contribute

There is a contribution type for every comfort level:

- **Bug reports** — If something is broken, an issue with clear reproduction steps is gold.
- **Feature requests** — Tell us what problem you are trying to solve, not just the solution you imagined.
- **Code** — Bug fixes, new features, refactors, performance improvements.
- **Agent templates** — Have a role we don't have yet? Contribute a template (legal, medical, finance — we especially need DACH-specific ones).
- **Skills** — Reusable capability modules (PDF parsing, invoice processing, contract diff, etc.).
- **Documentation** — Tutorials, architecture docs, fixing outdated examples.
- **Translations** — UI translations, especially DE/FR/ES/IT.
- **Testing** — More tests = fewer regressions. Especially happy-path E2E tests.
- **Design** — Screenshots, logo refinements, dark-mode tweaks.

## Dev Setup

Tested on macOS (Apple Silicon and Intel) and Linux (Ubuntu 22.04+). Windows works under WSL2.

### Prerequisites

- Git
- Docker Desktop (or Docker Engine 24+)
- Node.js 20+ (for frontend dev outside Docker)
- Python 3.12+ (for orchestrator dev outside Docker)
- 16 GB RAM recommended

### Setup steps

```bash
# 1. Fork on GitHub, then clone your fork
git clone https://github.com/YOUR-USERNAME/AI-Employee.git
cd AI-Employee

# 2. Add the upstream remote
git remote add upstream https://github.com/greeves89/AI-Employee.git

# 3. Copy the example env
cp .env.example .env

# 4. Generate required secrets
python -c "from cryptography.fernet import Fernet; print('ENCRYPTION_KEY=' + Fernet.generate_key().decode())" >> .env
echo "JWT_SECRET=$(openssl rand -base64 32)" >> .env

# 5. Add a Claude token to .env (OAuth or API key)
#    CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...   OR
#    ANTHROPIC_API_KEY=sk-ant-api-...

# 6. Start the dev stack
docker compose up --build

# 7. Open the UI
open http://localhost:3000
```

The first boot builds the agent image (~2 min) and runs DB migrations automatically.

### Running services individually (for faster iteration)

```bash
# Just the infrastructure
docker compose up postgres redis embedding-service

# Orchestrator (in another terminal)
cd orchestrator
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000

# Frontend (in another terminal)
cd frontend
npm install
npm run dev
```

## Project Structure

```
AI-Employee/
├── agent/                      # Docker image that runs Claude Code CLI
│   └── app/
│       ├── agent_runner.py     # Claude CLI wrapper (heart of the system)
│       └── mcp_servers/        # Memory, Knowledge, Notifications, etc.
├── orchestrator/               # FastAPI backend
│   └── app/
│       ├── api/                # REST + WebSocket routes
│       ├── core/               # agent_manager, load_balancer, etc.
│       ├── models/             # SQLAlchemy models
│       └── services/           # Business logic
├── frontend/                   # Next.js 14 web UI
│   └── src/
│       ├── app/                # App router pages
│       ├── components/         # React components (NO shadcn/ui)
│       └── lib/                # Utilities, API client
├── embedding-service/          # BAAI/bge-m3 embeddings FastAPI service
├── docs/                       # User + developer docs
├── scripts/                    # Setup, backup, migration scripts
├── monitoring/                 # Prometheus, Grafana, exporters
├── traefik/                    # Production reverse-proxy config
├── docker-compose.yml          # Dev stack
├── docker-compose.community.yml# Community edition stack
├── docker-compose.prod.yml     # Production stack
└── docker-compose.monitoring.yml # Observability overlay
```

Key files to know:

- `agent/app/agent_runner.py` — wraps the Claude Code CLI in headless mode
- `orchestrator/app/core/agent_manager.py` — Docker container lifecycle
- `orchestrator/app/core/load_balancer.py` — task distribution
- `orchestrator/app/api/ws.py` — WebSocket log streaming

## Development Workflow

### Branch naming

Use conventional prefixes:

- `feat/` — new features (`feat/meeting-rooms`, `feat/gemini-adapter`)
- `fix/` — bug fixes (`fix/agent-restart-race`)
- `docs/` — documentation (`docs/contributing-update`)
- `refactor/` — refactors without behavior change
- `test/` — test additions/fixes
- `chore/` — tooling, CI, deps

### Commit messages

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add Gemini 2.0 LLM adapter
fix: prevent race condition in agent restart
docs: update quick-start for Docker 25
refactor: extract approval rule evaluator
test: add E2E test for meeting rooms
chore: bump next to 14.2.15
```

Keep the subject under 72 characters. Use the body to explain *why*, not *what*.

### Pull request process

1. **Open an issue first** for non-trivial changes, so we can align on the approach before you invest hours.
2. **Fork and branch** from `main`.
3. **Write tests** for new behavior and for bug fixes.
4. **Run the full test suite** locally before opening the PR.
5. **Keep PRs small and focused.** Two small PRs beat one giant one.
6. **Fill out the PR template** (what changed, why, how to test).
7. **Respond to review feedback** — we try to review within 3 business days.

## Code Standards

### Python (orchestrator, agent, embedding-service)

- **Python 3.12+**
- **Type hints everywhere** (`from __future__ import annotations` at the top)
- **Black** for formatting (`black orchestrator/ agent/`)
- **Ruff** for linting (`ruff check orchestrator/ agent/`)
- **Async by default** — use `async`/`await` for I/O, avoid blocking the event loop
- **SQLAlchemy async** — use `AsyncSession`, not the sync session
- **Pydantic v2** for all request/response models
- **No print statements** — use the `logging` module
- **Alembic migrations** for every schema change (autogenerate + review by hand)

### TypeScript (frontend)

- **TypeScript strict mode** — no `any`, no `@ts-ignore` without a comment explaining why
- **Plain Tailwind CSS** — this project does **NOT** use shadcn/ui. Never import from `@/components/ui/*`
- **Radix UI** primitives for dialogs, dropdowns, tooltips
- **Framer Motion** for animations
- **lucide-react** for icons
- Use the `cn()` utility from `@/lib/utils` for conditional classes
- Follow existing card/button/badge/modal patterns (see `CLAUDE.md` for the style guide)
- Run `npm run build` before committing to verify no import errors

### General

- **Keep functions under 50 lines** when possible
- **Write self-documenting names** — long names beat short ones with comments
- **No dead code** — delete it, don't comment it out (git remembers)
- **No secrets in commits** — use `.env`, `.env.example` has placeholders

## Testing

```bash
# Orchestrator unit tests
cd orchestrator
pytest

# Orchestrator with coverage
pytest --cov=app --cov-report=html

# Frontend unit tests
cd frontend
npm test

# Frontend E2E (Playwright)
npm run test:e2e

# Full stack test
docker compose -f docker-compose.yml -f docker-compose.test.yml up --abort-on-container-exit
```

When adding a feature: aim for a unit test + an integration test. When fixing a bug: add a regression test that fails without your fix.

## Contributor License Agreement

By submitting a contribution (code, docs, templates, translations, etc.) to this project, you agree that:

1. Your contribution is your own original work (or you have the right to submit it).
2. Your contribution is licensed under the same **Sustainable Use License (Fair-Code)** as the rest of the project — see [LICENSE.md](LICENSE.md).
3. You grant the project maintainers a perpetual, worldwide, non-exclusive, royalty-free license to use, modify, distribute, and sublicense your contribution as part of AI-Employee — including under a future dual-licensing arrangement (commercial license for SaaS hosting).

We do not ask you to assign copyright. You keep ownership of your contributions. This agreement simply ensures the project can continue to be developed sustainably.

## Recognition

Every contributor gets:

- Their name in the `CONTRIBUTORS.md` file (added on first merged PR)
- A shout-out in the release notes when their work ships
- Our sincere thanks

Significant contributors may be invited to become maintainers with write access to the repo.

## Getting Help

- **Architecture questions** → open a Discussion on GitHub
- **Bug reports** → open an Issue with reproduction steps
- **Security issues** → email **security@ai-employee.dev** (do **not** open a public issue, see [SECURITY.md](SECURITY.md))
- **Commercial licensing** → email **licensing@ai-employee.dev**
- **General chat** → Discord (placeholder link in README)

## Code of Conduct

Be kind. Be patient. Assume good faith. Disagree respectfully. No personal attacks, no harassment, no gatekeeping. We are all here to build something useful together.

If someone makes the community feel unsafe, report it to the maintainers and we will act.

---

Thanks again for contributing. Every PR, issue, and kind word makes this project better.
