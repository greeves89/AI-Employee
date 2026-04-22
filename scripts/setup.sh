#!/bin/bash
# AI Employee Platform — Full Setup Script
# Usage: ./scripts/setup.sh
# Idempotent: safe to run multiple times.
set -euo pipefail

# ── colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC}    $*"; }
info() { echo -e "${CYAN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()  { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── working directory ─────────────────────────────────────────────────────────
cd "$(dirname "$0")/.."

echo ""
echo "╔══════════════════════════════════════╗"
echo "║   AI Employee Platform — Setup       ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ── 1. Prerequisites ──────────────────────────────────────────────────────────
info "Checking prerequisites..."

command -v docker &>/dev/null         || die "Docker not installed. Get it at https://docs.docker.com/get-docker/"
docker info &>/dev/null 2>&1          || die "Docker is not running. Please start Docker Desktop."
command -v python3 &>/dev/null        || die "python3 not found."
docker compose version &>/dev/null 2>&1 || die "docker compose plugin not found. You may have the legacy 'docker-compose' (v1). Please update Docker Desktop to get the 'docker compose' plugin (v2): https://docs.docker.com/compose/migrate/"

ok "Docker is running"

# ── 2. .env ───────────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
    cp .env.example .env
    info "Created .env from .env.example"
fi

# Auto-generate API_SECRET_KEY if missing, empty, or still default
if ! grep -qE "^API_SECRET_KEY=.{32,}" .env; then
    SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
    if grep -q "^API_SECRET_KEY=" .env; then
        sed -i.bak "s|^API_SECRET_KEY=.*|API_SECRET_KEY=${SECRET}|" .env && rm -f .env.bak
    else
        echo "API_SECRET_KEY=${SECRET}" >> .env
    fi
    ok "Generated API_SECRET_KEY"
fi

# Auto-generate ENCRYPTION_KEY if empty
if grep -qE "^ENCRYPTION_KEY=\s*$" .env; then
    ENC_KEY=$(python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode() + '=')")
    sed -i.bak "s|^ENCRYPTION_KEY=.*|ENCRYPTION_KEY=${ENC_KEY}|" .env && rm -f .env.bak
    ok "Generated ENCRYPTION_KEY"
fi

# Auto-generate DB_PASSWORD if still default
if grep -qE "^DB_PASSWORD=devpassword" .env; then
    DB_PASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
    sed -i.bak "s|^DB_PASSWORD=.*|DB_PASSWORD=${DB_PASS}|" .env && rm -f .env.bak
    sed -i.bak "s|^DATABASE_URL=postgresql+asyncpg://ai_employee:.*@|DATABASE_URL=postgresql+asyncpg://ai_employee:${DB_PASS}@|" .env && rm -f .env.bak
    ok "Generated DB_PASSWORD"
fi

# Auto-generate REDIS_PASSWORD if still default
if grep -qE "^REDIS_PASSWORD=changeme-redis-password" .env; then
    REDIS_PASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
    sed -i.bak "s|^REDIS_PASSWORD=.*|REDIS_PASSWORD=${REDIS_PASS}|" .env && rm -f .env.bak
    sed -i.bak "s|^REDIS_URL=redis://:.*@|REDIS_URL=redis://:${REDIS_PASS}@|" .env && rm -f .env.bak
    ok "Generated REDIS_PASSWORD"
fi

ok ".env ready"

# ── 3. Claude auth ────────────────────────────────────────────────────────────
HAS_API_KEY=$(grep -E "^ANTHROPIC_API_KEY=.+" .env || true)
HAS_OAUTH=$(grep -E "^CLAUDE_CODE_OAUTH_TOKEN=.+" .env || true)

if [ -z "$HAS_API_KEY" ] && [ -z "$HAS_OAUTH" ]; then
    warn "No Claude authentication found in .env — add ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN before creating agents."
else
    ok "Claude auth configured"
fi


# ── 5. Shared volume ──────────────────────────────────────────────────────────
docker volume inspect ai-employee-shared &>/dev/null || docker volume create ai-employee-shared
ok "Shared volume ready"

# ── 6. Build images ───────────────────────────────────────────────────────────
echo ""
info "Building Docker images (this takes a few minutes on first run)..."
docker build -t ai-employee-agent:latest ./agent
docker compose build
ok "Images built"

# ── 7. Start stack ────────────────────────────────────────────────────────────
info "Starting all services..."
docker compose up -d
ok "Services started"

# ── 7. Wait for orchestrator ──────────────────────────────────────────────────
info "Waiting for orchestrator to be ready..."
RETRIES=40
until curl -sf http://localhost:8000/health >/dev/null 2>&1; do
    RETRIES=$((RETRIES - 1))
    [ $RETRIES -le 0 ] && die "Orchestrator did not start in time. Run: docker compose logs orchestrator"
    sleep 3
done
ok "Orchestrator is ready"

# ── 8. Done ───────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║               Setup Complete!                    ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""
echo -e "  Web UI:   ${CYAN}http://localhost:3000${NC}"
echo -e "  API docs: ${CYAN}http://localhost:8000/docs${NC}"
echo ""
info "Open the Web UI and create your admin account to get started."
echo ""
