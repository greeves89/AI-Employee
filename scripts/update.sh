#!/bin/bash
# AI Employee Platform — Update Script
# Usage: ./scripts/update.sh
#
# Rebuilds EVERY image that ships code, including the dynamically-launched
# agent image (ai-employee-agent:latest) which `docker compose up --build`
# never touches because it is not a compose service (see issue #433).
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
echo "║   AI Employee Platform — Update      ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ── 1. Prerequisites ──────────────────────────────────────────────────────────
info "Checking prerequisites..."
command -v docker &>/dev/null           || die "Docker not installed. Get it at https://docs.docker.com/get-docker/"
docker info &>/dev/null 2>&1            || die "Docker is not running. Please start Docker Desktop."
docker compose version &>/dev/null 2>&1 || die "docker compose plugin not found. Update Docker Desktop for the 'docker compose' plugin (v2)."
command -v git &>/dev/null              || die "git not found."
ok "Docker is running"

# ── 2. Pull latest code ───────────────────────────────────────────────────────
info "Pulling latest code..."
git pull
ok "Code up to date"

# ── 3. Rebuild the agent image (NOT a compose service — must be built here) ────
# Capture the current agent image id so we can tell which running agents are
# still on the old image after the rebuild.
OLD_AGENT_IMAGE_ID="$(docker images -q ai-employee-agent:latest 2>/dev/null || true)"
info "Rebuilding agent image (ai-employee-agent:latest)..."
docker build -t ai-employee-agent:latest ./agent
NEW_AGENT_IMAGE_ID="$(docker images -q ai-employee-agent:latest 2>/dev/null || true)"
if [ -n "$OLD_AGENT_IMAGE_ID" ] && [ "$OLD_AGENT_IMAGE_ID" = "$NEW_AGENT_IMAGE_ID" ]; then
    ok "Agent image unchanged"
else
    ok "Agent image rebuilt ($NEW_AGENT_IMAGE_ID)"
fi

# ── 4. Rebuild + restart the compose services (orchestrator, frontend, ...) ────
info "Rebuilding and restarting the stack..."
docker compose up -d --build
ok "Stack restarted"

# ── 5. Wait for orchestrator ──────────────────────────────────────────────────
info "Waiting for orchestrator to be ready..."
RETRIES=40
until curl -sf http://localhost:8000/health >/dev/null 2>&1; do
    RETRIES=$((RETRIES - 1))
    [ $RETRIES -le 0 ] && die "Orchestrator did not start in time. Run: docker compose logs orchestrator"
    sleep 3
done
ok "Orchestrator is ready"

# ── 6. Report agents still running the OLD image ──────────────────────────────
# Agent containers are launched dynamically and labelled ai-employee.type=agent.
# A rebuild retags ai-employee-agent:latest to a new id but leaves running
# agents on the old one — they need an explicit recreate (which interrupts
# running work, so it is left to the operator, per issue #433 "Not in scope").
echo ""
info "Checking which agents are still on the old image..."
STALE_AGENTS=""
AGENT_CONTAINERS="$(docker ps --filter "label=ai-employee.type=agent" --format '{{.ID}} {{.Names}}' 2>/dev/null || true)"
if [ -z "$AGENT_CONTAINERS" ]; then
    ok "No running agent containers found — nothing to recreate."
else
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        cid="${line%% *}"
        cname="${line#* }"
        running_img="$(docker inspect "$cid" --format '{{.Image}}' 2>/dev/null | sed 's/^sha256://')"
        if [ -n "$NEW_AGENT_IMAGE_ID" ] && [ "${running_img#$NEW_AGENT_IMAGE_ID}" = "$running_img" ]; then
            STALE_AGENTS="${STALE_AGENTS}  • ${cname}\n"
        fi
    done <<< "$AGENT_CONTAINERS"

    if [ -z "$STALE_AGENTS" ]; then
        ok "All running agents are on the latest image."
    else
        warn "These agents are still running the OLD image and need to be recreated:"
        echo -e "$STALE_AGENTS"
        warn "Recreate them (this interrupts their running work) via either:"
        warn "  • the Web UI: agent list → 'Update' action per agent, or"
        warn "  • the API:    POST /agents/{id}/update  (preserves all data)"
    fi
fi

# ── 7. Done ───────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║               Update Complete!                   ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""
