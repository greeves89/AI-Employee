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

# Auto-generate API_SECRET_KEY if empty
if grep -qE "^API_SECRET_KEY=\s*$" .env; then
    SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
    # macOS/Linux compatible sed
    sed -i.bak "s|^API_SECRET_KEY=.*|API_SECRET_KEY=${SECRET}|" .env && rm -f .env.bak
    ok "Generated API_SECRET_KEY"
fi

# Auto-generate ENCRYPTION_KEY if empty
if grep -qE "^ENCRYPTION_KEY=\s*$" .env; then
    ENC_KEY=$(python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode() + '=')")
    sed -i.bak "s|^ENCRYPTION_KEY=.*|ENCRYPTION_KEY=${ENC_KEY}|" .env && rm -f .env.bak
    ok "Generated ENCRYPTION_KEY"
fi

ok ".env ready"

# ── 3. Claude auth ────────────────────────────────────────────────────────────
HAS_API_KEY=$(grep -E "^ANTHROPIC_API_KEY=.+" .env || true)
HAS_OAUTH=$(grep -E "^CLAUDE_CODE_OAUTH_TOKEN=.+" .env || true)

if [ -z "$HAS_API_KEY" ] && [ -z "$HAS_OAUTH" ]; then
    echo ""
    warn "No Claude authentication found in .env!"
    echo "  Agents need one of:"
    echo "    ANTHROPIC_API_KEY=sk-ant-...   (from console.anthropic.com)"
    echo "    CLAUDE_CODE_OAUTH_TOKEN=...    (from 'claude login' → macOS Keychain)"
    echo ""
    read -rp "Paste your ANTHROPIC_API_KEY (or press Enter to skip): " USER_KEY
    if [ -n "$USER_KEY" ]; then
        sed -i.bak "s|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=${USER_KEY}|" .env && rm -f .env.bak
        ok "API key saved to .env"
    else
        warn "Skipped — agents won't work until you add a key to .env"
    fi
else
    ok "Claude auth configured"
fi

# ── 4. Admin account ──────────────────────────────────────────────────────────
echo ""
info "Admin account setup (used to log in to the web UI)"

read -rp "  Admin email    [admin@example.com]: " ADMIN_EMAIL
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@example.com}"

read -rp "  Admin name     [Admin]: " ADMIN_NAME
ADMIN_NAME="${ADMIN_NAME:-Admin}"

# Generate a random password if user just hits enter
DEFAULT_PW=$(python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(16)))")
read -rsp "  Admin password [random — shown at end]: " ADMIN_PASSWORD
echo ""
ADMIN_PASSWORD="${ADMIN_PASSWORD:-$DEFAULT_PW}"

# ── 5. Build images ───────────────────────────────────────────────────────────
echo ""
info "Building Docker images (this takes a few minutes on first run)..."
docker build -t ai-employee-agent:latest ./agent
docker compose build
ok "Images built"

# ── 6. Start stack ────────────────────────────────────────────────────────────
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

# ── 8. Create first admin user ────────────────────────────────────────────────
info "Creating admin account..."
HTTP_STATUS=$(curl -s -o /tmp/ai_setup_reg.json -w "%{http_code}" \
    -X POST http://localhost:8000/api/v1/auth/register \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"${ADMIN_EMAIL}\",\"password\":\"${ADMIN_PASSWORD}\",\"name\":\"${ADMIN_NAME}\"}")

if [ "$HTTP_STATUS" = "200" ]; then
    ok "Admin account created"
elif [ "$HTTP_STATUS" = "409" ]; then
    ok "Admin account already exists — skipping"
else
    DETAIL=$(python3 -c "import json,sys; d=json.load(open('/tmp/ai_setup_reg.json')); print(d.get('detail','unknown'))" 2>/dev/null || echo "unknown")
    warn "Registration returned HTTP ${HTTP_STATUS}: ${DETAIL}"
    warn "You can create an account manually at http://localhost:3000"
fi

# ── 9. Done ───────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║               Setup Complete!                    ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""
echo -e "  Web UI:   ${CYAN}http://localhost:3000${NC}"
echo -e "  API docs: ${CYAN}http://localhost:8000/docs${NC}"
echo ""
echo -e "  Email:    ${CYAN}${ADMIN_EMAIL}${NC}"
echo -e "  Password: ${CYAN}${ADMIN_PASSWORD}${NC}"
echo ""
warn "Save the password above — it won't be shown again!"
echo ""
