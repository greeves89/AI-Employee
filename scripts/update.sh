#!/bin/bash
# AI Employee Platform — Update Script
# Usage: ./scripts/update.sh [--ohne-sicherung] [--lokal-bauen]
#
# Sichert vorher automatisch (scripts/backup.sh) und bricht ab, wenn das
# scheitert. Abschalten: --ohne-sicherung oder UPDATE_OHNE_SICHERUNG=1.
#
# Abbilder (#889): holt die fertigen Abbilder genau dieser Version aus der
# Registry (AI_EMPLOYEE_REGISTRY, Standard ghcr.io/greeves89) — auch das
# Agenten-Abbild, das kein Compose-Dienst ist (#433). Nur wenn die Registry
# nicht erreichbar ist oder --lokal-bauen gesetzt ist, wird wie frueher lokal
# gebaut. Idempotent: safe to run multiple times.
set -euo pipefail

# ── colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC}    $*"; }
info() { echo -e "${CYAN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()  { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── working directory ─────────────────────────────────────────────────────────
cd "$(dirname "$0")/.."

OHNE_SICHERUNG="${UPDATE_OHNE_SICHERUNG:-0}"
LOKAL_BAUEN="${UPDATE_LOKAL_BAUEN:-0}"
for arg in "$@"; do
    case "$arg" in
        --ohne-sicherung) OHNE_SICHERUNG=1 ;;
        --lokal-bauen) LOKAL_BAUEN=1 ;;
        *) die "Unbekannte Option: $arg" ;;
    esac
done

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

# ── 2. Sicherung vor dem Update (#892) ────────────────────────────────────────
# Ein Update ändert Datenbankschema und Abbilder. Geht dabei etwas schief, ist
# die Sicherung von vorhin der Rückweg. Abschaltbar, aber nicht stillschweigend.
# Zweiter Durchgang nach einem Neustart mit der neuen Fassung (siehe Schritt 3):
# Sicherung und Pull sind dann schon erledigt.
NEU_GESTARTET="${UPDATE_NEU_GESTARTET:-0}"
VORHER_COMMIT="${UPDATE_VORHER_COMMIT:-$(git rev-parse --short HEAD 2>/dev/null || echo unbekannt)}"
if [ "$NEU_GESTARTET" = "1" ]; then
    info "Neue Fassung von update.sh läuft — Sicherung und Pull sind schon erledigt."
elif [ "$OHNE_SICHERUNG" = "1" ]; then
    warn "Sicherung vor dem Update übersprungen (--ohne-sicherung / UPDATE_OHNE_SICHERUNG=1)."
else
    info "Sichere vor dem Update (scripts/backup.sh)..."
    ./scripts/backup.sh \
        || die "Sicherung fehlgeschlagen — Update abgebrochen, nichts verändert. Ursache beheben oder bewusst mit --ohne-sicherung aktualisieren."
    ok "Sicherung erstellt"
fi

# ── 3. Pull latest code ───────────────────────────────────────────────────────
if [ "$NEU_GESTARTET" != "1" ]; then
    SKRIPT_VORHER="$(git hash-object scripts/update.sh 2>/dev/null || true)"
    info "Pulling latest code..."
    git pull
    ok "Code up to date (vorher: ${VORHER_COMMIT})"
    # Bringt der Pull eine neue Fassung DIESES Skripts, liefe sonst die alte
    # weiter: bash liest aus der bereits geoeffneten (alten) Datei. So geschehen
    # beim Umstieg auf die Registry-Abbilder (v1.364): das Update baute noch lokal.
    if [ "$(git hash-object scripts/update.sh 2>/dev/null || true)" != "$SKRIPT_VORHER" ]; then
        info "update.sh hat sich mit dem Update geändert — starte die neue Fassung."
        UPDATE_NEU_GESTARTET=1 UPDATE_VORHER_COMMIT="$VORHER_COMMIT" exec ./scripts/update.sh "$@"
    fi
fi

# ── 4. Abbilder: aus der Registry holen, sonst lokal bauen (#889) ─────────────
# Capture the current agent image id so we can tell which running agents are
# still on the old image afterwards.
OLD_AGENT_IMAGE_ID="$(docker images -q ai-employee-agent:latest 2>/dev/null || true)"
# shellcheck source=lib/abbilder.sh
. ./scripts/lib/abbilder.sh
abbilder_bereitstellen "$LOKAL_BAUEN"
NEW_AGENT_IMAGE_ID="$(docker images -q ai-employee-agent:latest 2>/dev/null || true)"
if [ -n "$OLD_AGENT_IMAGE_ID" ] && [ "$OLD_AGENT_IMAGE_ID" = "$NEW_AGENT_IMAGE_ID" ]; then
    ok "Agent image unchanged"
else
    ok "Agent image updated ($NEW_AGENT_IMAGE_ID, Quelle: ${ABBILD_QUELLE})"
fi

# ── 5. Restart the compose services (orchestrator, frontend, ...) ─────────────
info "Restarting the stack..."
docker compose up -d
ok "Stack restarted"

# ── 6. Wait for orchestrator ──────────────────────────────────────────────────
info "Waiting for orchestrator to be ready..."
RETRIES=40
until curl -sf http://localhost:8000/health >/dev/null 2>&1; do
    RETRIES=$((RETRIES - 1))
    [ $RETRIES -le 0 ] && die "Orchestrator did not start in time. Run: docker compose logs orchestrator"
    sleep 3
done
ok "Orchestrator is ready"

# ── 6b. Alte Abbilder wegräumen (erst nach erfolgreichem Start) ───────────────
# Behalten: die neue Version und die vorige für den Rücksprung (siehe unten).
VORHER_VERSION="$(git show "${VORHER_COMMIT}:VERSION" 2>/dev/null | tr -d '[:space:]' || true)"
info "Räume alte Abbilder auf..."
abbilder_aufraeumen "${AI_EMPLOYEE_VERSION:-}" "${VORHER_VERSION}" || warn "Aufräumen nicht vollständig — Update ist trotzdem fertig."

# ── 6b. Netzwächter: neuen Code übernehmen bzw. auf ihn hinweisen ─────────────
# Er läuft auf dem Host (systemd) und liest scripts/netzwaechter.py direkt aus dem
# Repo — nach dem Pull also neu starten, sonst läuft der alte Stand weiter.
if systemctl is-enabled ai-employee-netzwaechter >/dev/null 2>&1; then
    if sudo -n systemctl restart ai-employee-netzwaechter 2>/dev/null; then
        ok "Netzwächter neu gestartet"
    else
        warn "Netzwächter nicht neu gestartet (kein sudo ohne Passwort): sudo systemctl restart ai-employee-netzwaechter"
    fi
else
    warn "Netzwächter ist nicht eingerichtet — Agenten erreichen Firmennetz, Router und Host."
    warn "  Einrichten: sudo ./scripts/install-netzwaechter.sh   (Vorschau: --zeigen)"
fi

# ── 7. Report agents still running the OLD image ──────────────────────────────
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
        running_img="$(docker inspect "$cid" --format '{{.Image}}' 2>/dev/null | sed 's/^sha256://' || true)"
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

# ── 8. Done ───────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║               Update Complete!                   ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""
info "Rücksprung, falls nötig: git checkout ${VORHER_COMMIT}, dann"
info "  bash -c '. scripts/lib/abbilder.sh && abbilder_bereitstellen 0' && docker compose up -d"
info "  und die Sicherung von vorhin mit ./scripts/restore.sh --backup <ordner> zurückspielen"
info "  (das Datenbankschema kann sich geändert haben). Handbuch: Betrieb & Datenschutz."
