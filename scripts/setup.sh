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
command -v git &>/dev/null            || die "git not found (needed for updates). Ubuntu/Debian: sudo apt install git"
python3 -c "import cryptography" 2>/dev/null \
                                      || die "python3-cryptography not found (needed to create the encryption key). Ubuntu/Debian: sudo apt install python3-cryptography"

# Docker 26+: older engines cannot mount per-user subfolders of a volume, so the
# platform could not keep users' files apart (Admin → Betrieb → Sicherheitsstatus
# reports < 26 as critical). Compose 2.20+ for the features docker-compose.yml uses.
DOCKER_HAUPT=$(docker version --format '{{.Server.Version}}' 2>/dev/null | cut -d. -f1)
[[ "${DOCKER_HAUPT:-0}" =~ ^[0-9]+$ ]] && (( DOCKER_HAUPT >= 26 )) \
                                      || die "Docker ${DOCKER_HAUPT:-?}.x is too old — version 26 or newer is required. Update: https://docs.docker.com/engine/install/"
COMPOSE_V=$(docker compose version --short 2>/dev/null | sed 's/^v//')
COMPOSE_HAUPT=${COMPOSE_V%%.*}; COMPOSE_REST=${COMPOSE_V#*.}; COMPOSE_NEBEN=${COMPOSE_REST%%.*}
{ [[ "$COMPOSE_HAUPT" =~ ^[0-9]+$ && "$COMPOSE_NEBEN" =~ ^[0-9]+$ ]] \
  && (( COMPOSE_HAUPT > 2 || (COMPOSE_HAUPT == 2 && COMPOSE_NEBEN >= 20) )); } \
                                      || die "Docker Compose ${COMPOSE_V:-?} is too old — version 2.20 or newer is required."

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

# Auto-generate or repair ENCRYPTION_KEY — must be a valid Fernet key (44 base64 chars)
CURRENT_ENC=$(grep -E "^ENCRYPTION_KEY=" .env | head -1 | cut -d'=' -f2-)
NEED_REGEN=0
if [ -z "$CURRENT_ENC" ]; then
    NEED_REGEN=1
elif ! python3 -c "from cryptography.fernet import Fernet; Fernet('$CURRENT_ENC'.encode())" 2>/dev/null; then
    warn "Existing ENCRYPTION_KEY is invalid — regenerating. Any previously-encrypted secrets become unrecoverable."
    NEED_REGEN=1
fi
if [ "$NEED_REGEN" = "1" ]; then
    ENC_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
    if grep -q "^ENCRYPTION_KEY=" .env; then
        sed -i.bak "s|^ENCRYPTION_KEY=.*|ENCRYPTION_KEY=${ENC_KEY}|" .env && rm -f .env.bak
    else
        echo "ENCRYPTION_KEY=${ENC_KEY}" >> .env
    fi
    ok "Generated valid ENCRYPTION_KEY (Fernet)"
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

# Lokaler Schlüssel für den Herzschlag der Datensicherung (#892)
# shellcheck source=scripts/lib/sicherung.sh
. scripts/lib/sicherung.sh
if backup_token_sicherstellen .env; then
    ok "Generated BACKUP_STATUS_TOKEN"
fi
if egress_token_sicherstellen .env; then
    ok "Generated EGRESS_PROXY_TOKEN"
fi
if waechter_token_sicherstellen .env; then
    ok "DOCKER_WAECHTER_TOKEN angelegt (Docker-Wächter)"
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

# ── 6. Abbilder: fertig aus der Registry, sonst lokal bauen (#889) ─────────────
echo ""
info "Stelle Docker-Abbilder bereit (lokaler Bau nur als Rückfall, dauert dann einige Minuten)..."
# shellcheck source=lib/abbilder.sh
. ./scripts/lib/abbilder.sh
abbilder_bereitstellen "${SETUP_LOKAL_BAUEN:-0}"
ok "Abbilder bereit (Quelle: ${ABBILD_QUELLE})"

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

# ── 7b. Wait for embedding service (bge-m3 model, ~2.3 GB on first boot) ─────
info "Waiting for embedding service (bge-m3, semantic search)..."
info "First boot downloads the model — this takes 1–3 minutes."
EMB_RETRIES=80  # 80 × 3s = 4 minutes max
until docker exec ai-employee-orchestrator curl -sf http://embedding-service:8001/healthz >/dev/null 2>&1; do
    EMB_RETRIES=$((EMB_RETRIES - 1))
    if [ $EMB_RETRIES -le 0 ]; then
        warn "Embedding service did not become ready in time. Semantic search will fall back to keyword."
        warn "Check logs: docker compose logs embedding-service"
        break
    fi
    sleep 3
done
if [ $EMB_RETRIES -gt 0 ]; then
    ok "Embedding service ready (semantic search enabled)"
fi

# ── 7c. Wait for STT service (faster-whisper model, ~480 MB on first boot) ──
info "Waiting for STT service (faster-whisper, voice transcription)..."
STT_RETRIES=60  # 60 × 3s = 3 minutes max
until docker exec ai-employee-orchestrator curl -sf http://stt-service:8003/healthz >/dev/null 2>&1; do
    STT_RETRIES=$((STT_RETRIES - 1))
    if [ $STT_RETRIES -le 0 ]; then
        warn "STT service did not become ready in time. Voice transcription may be delayed on first use."
        warn "Check logs: docker compose logs stt-service"
        break
    fi
    sleep 3
done
if [ $STT_RETRIES -gt 0 ]; then
    ok "STT service ready (Telegram voice transcription enabled)"
fi

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
