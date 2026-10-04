#!/usr/bin/env bash
# backup.sh — Datensicherung der AI-Employee-Anlage (#892)
#
# Sichert, was man nach einem Plattentod braucht:
#   1. Datenbank (pg_dump — konsistent im laufenden Betrieb)
#   2. Volumes (Liste in scripts/lib/sicherung.sh, dynamisch eingesammelt):
#      Arbeitsordner der Agenten (workspace-*), Sitzungen (claude-session-*),
#      Build-Werkzeuge (build-tools-*), gemeinsamer Ordner (ai-employee-shared),
#      Redis und Rückmeldungen
#   3. Konfiguration: .env und orchestrator/data/ — dort liegt der
#      Verschlüsselungsschlüssel. Ohne ihn sind nach dem Rückspielen alle
#      verschlüsselten Geheimnisse (KI-Konten, Keys, Zugänge) unlesbar.
#
# Die Sicherung enthält damit Geheimnisse: Ordner 700, Dateien 600. Wer sie
# außer Haus bringt, verschlüsselt sie dort (z. B. restic, borg).
#
# Aufbewahrung: 7 tägliche, 4 wöchentliche (Sonntag).
#
# Am Ende meldet das Skript einen Herzschlag an POST /api/v1/admin/backup-status
# (Schlüssel BACKUP_STATUS_TOKEN aus der .env). Die Karte „Datensicherung“
# unter Admin → Betrieb zeigt dann die letzte Sicherung; ein gescheiterter Lauf
# wird ebenfalls gemeldet.
#
# Aufruf:
#   ./scripts/backup.sh [--dest /pfad/zum/sicherungsordner]
#
# Umgebungsvariablen:
#   BACKUP_DIR            Ziel (Standard: /var/backups/ai-employee)
#   COMPOSE_PROJECT_NAME  Compose-Projekt (Standard: am Postgres-Container abgelesen)
#   ORCHESTRATOR_URL      Für den Herzschlag (Standard: http://127.0.0.1:8000)
#   BACKUP_KEEP_DAILY / BACKUP_KEEP_WEEKLY   Aufbewahrung (7 / 4)
#
# Rückspielen: ./scripts/restore.sh --backup <ordner>

set -euo pipefail
umask 077

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/sicherung.sh
. "${INSTALL_DIR}/scripts/lib/sicherung.sh"

BACKUP_DIR="${BACKUP_DIR:-/var/backups/ai-employee}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dest) BACKUP_DIR="$2"; shift 2 ;;
        *) echo "Unbekannte Option: $1"; exit 1 ;;
    esac
done

ENV_DATEI="${INSTALL_DIR}/.env"
PG_CONTAINER="${PG_CONTAINER:-ai-employee-postgres}"
POSTGRES_USER="${POSTGRES_USER:-ai_employee}"
POSTGRES_DB="${POSTGRES_DB:-ai_employee}"
ORCHESTRATOR_URL="${ORCHESTRATOR_URL:-http://127.0.0.1:$(env_wert "$ENV_DATEI" ORCHESTRATOR_PORT | grep -E '^[0-9]+$' || echo 8000)}"
KEEP_DAILY="${BACKUP_KEEP_DAILY:-7}"
KEEP_WEEKLY="${BACKUP_KEEP_WEEKLY:-4}"
ALPINE_IMAGE="${ALPINE_IMAGE:-alpine:3.21}"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_TYPE="daily"
[ "$(date +%u)" = "7" ] && BACKUP_TYPE="weekly"
BACKUP_PATH="${BACKUP_DIR}/${BACKUP_TYPE}/${TIMESTAMP}"
LOG="${BACKUP_DIR}/backup.log"
START=$(date +%s)
SCHRITT="Vorbereitung"
VOLUME_ANZAHL=0

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

# Herzschlag an den Orchestrator. Scheitert er, ist die Sicherung trotzdem
# gültig — deshalb nur eine Warnung, nie ein Abbruch.
melde() {
    local status="$1" schritt="${2:-}" token json groesse dauer
    token=$(env_wert "$ENV_DATEI" BACKUP_STATUS_TOKEN)
    if [ -z "$token" ]; then
        log "Hinweis: BACKUP_STATUS_TOKEN fehlt in .env — die Admin-Karte erfährt nichts von dieser Sicherung."
        return 0
    fi
    command -v curl >/dev/null 2>&1 || { log "Hinweis: curl fehlt — kein Herzschlag."; return 0; }
    dauer=$(( $(date +%s) - START ))
    if [ "$status" = "ok" ]; then
        groesse=$(du -sk "$BACKUP_PATH" | awk '{print $1 * 1024}')
        json="{\"status\":\"ok\",\"groesse_bytes\":${groesse},\"dauer_s\":${dauer},\"volumes\":${VOLUME_ANZAHL}}"
    else
        json="{\"status\":\"fehler\",\"schritt\":\"${schritt}\",\"dauer_s\":${dauer}}"
    fi
    # Der Schlüssel geht über stdin (`-H @-`), nicht über die Befehlszeile: die
    # sieht jeder Nutzer des Hosts mit `ps`. printf ist eingebaut — kein eigener
    # Prozess, also auch dort nicht sichtbar.
    if ! printf 'X-Backup-Token: %s\n' "$token" | curl -fsS -m 15 -X POST \
        "${ORCHESTRATOR_URL}/api/v1/admin/backup-status" \
        -H "Content-Type: application/json" -H @- \
        -d "$json" >/dev/null 2>&1; then
        log "WARNUNG: Herzschlag an ${ORCHESTRATOR_URL} nicht zugestellt (Orchestrator aus oder Schlüssel noch nicht übernommen)."
    fi
}

beim_ende() {
    local rc=$?
    if [ "$rc" -ne 0 ]; then
        log "FEHLER: Sicherung abgebrochen im Schritt „${SCHRITT}“ (Exit ${rc})."
        melde fehler "$SCHRITT" || true
    fi
}
trap beim_ende EXIT

die() { log "FEHLER: $*"; exit 1; }

command -v docker >/dev/null 2>&1 || die "docker nicht gefunden"
command -v gzip >/dev/null 2>&1 || die "gzip nicht gefunden"

if backup_token_sicherstellen "$ENV_DATEI"; then
    log "BACKUP_STATUS_TOKEN in .env angelegt — der Orchestrator übernimmt ihn beim nächsten Neustart (docker compose up -d orchestrator)."
fi

mkdir -p "${BACKUP_PATH}/volumes"
chmod 700 "$BACKUP_PATH" "${BACKUP_PATH}/volumes"
log "=== Sicherung gestartet: ${BACKUP_TYPE} -> ${BACKUP_PATH} ==="

# ─── 1. Datenbank ─────────────────────────────────────────────────────────────
SCHRITT="Datenbank"
[ -n "$(docker ps -q --filter "name=^${PG_CONTAINER}\$")" ] \
    || die "Postgres-Container ${PG_CONTAINER} läuft nicht — Anlage starten (docker compose up -d postgres)."
PROJEKT=$(compose_projekt "$INSTALL_DIR")

log "Datenbank ${POSTGRES_DB} …"
docker exec "$PG_CONTAINER" pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" \
    | gzip > "${BACKUP_PATH}/postgres.sql.gz"
log "Datenbank gesichert ($(du -sh "${BACKUP_PATH}/postgres.sql.gz" | cut -f1))"

# ─── 2. Volumes ───────────────────────────────────────────────────────────────
SCHRITT="Volumes"
AUSWAHL=$(docker volume ls --format '{{.Name}}' | volumes_auswaehlen "$PROJEKT")
while read -r archiv volume; do
    [ -z "${archiv:-}" ] && continue
    log "Volume ${volume} …"
    docker run --rm \
        -v "${volume}:/data:ro" \
        -v "${BACKUP_PATH}/volumes:/backup" \
        "$ALPINE_IMAGE" \
        tar czf "/backup/${archiv}.tar.gz" -C /data .
    VOLUME_ANZAHL=$((VOLUME_ANZAHL + 1))
done <<< "$AUSWAHL"
log "${VOLUME_ANZAHL} Volume(s) gesichert"

# ─── 3. Konfiguration und Schlüssel ───────────────────────────────────────────
SCHRITT="Konfiguration"
TEILE=""
[ -f "${INSTALL_DIR}/.env" ] && TEILE=".env"
[ -d "${INSTALL_DIR}/orchestrator/data" ] && TEILE="${TEILE} orchestrator/data"
if [ -z "$TEILE" ]; then
    die ".env und orchestrator/data/ fehlen — ohne Schlüssel ist eine Sicherung wertlos."
fi
[ -f "${INSTALL_DIR}/orchestrator/data/.encryption_key" ] || [ -n "$(env_wert "$ENV_DATEI" ENCRYPTION_KEY)" ] \
    || log "WARNUNG: weder ENCRYPTION_KEY in .env noch orchestrator/data/.encryption_key gefunden."
# shellcheck disable=SC2086  # TEILE ist eine bewusst getrennte Liste
tar czf "${BACKUP_PATH}/konfiguration.tar.gz" -C "$INSTALL_DIR" $TEILE
log "Konfiguration gesichert (${TEILE# })"

# ─── 4. Manifest ──────────────────────────────────────────────────────────────
SCHRITT="Manifest"
{
    echo "format=2"
    echo "timestamp=${TIMESTAMP}"
    echo "backup_type=${BACKUP_TYPE}"
    echo "postgres_db=${POSTGRES_DB}"
    echo "compose_projekt=${PROJEKT}"
    echo "version=$(cat "${INSTALL_DIR}/VERSION" 2>/dev/null || echo unbekannt)"
    echo "hostname=$(hostname)"
    echo "volumes=${VOLUME_ANZAHL}"
    # Relative Pfade: die Prüfung klappt auch, wenn die Sicherung verschoben wurde.
    (cd "$BACKUP_PATH" && find . -type f ! -name MANIFEST | sort | while read -r f; do pruefsumme "$f"; done)
} > "${BACKUP_PATH}/MANIFEST"
find "$BACKUP_PATH" -type f -exec chmod 600 {} +

# ─── 5. Aufbewahrung ──────────────────────────────────────────────────────────
SCHRITT="Aufbewahrung"
aufraeumen() {
    local art="$1" behalten="$2" basis="${BACKUP_DIR}/$1" anzahl
    [ -d "$basis" ] || return 0
    anzahl=$(find "$basis" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')
    if [ "$anzahl" -gt "$behalten" ]; then
        log "Entferne $((anzahl - behalten)) alte ${art}-Sicherung(en) (behalte ${behalten})"
        find "$basis" -mindepth 1 -maxdepth 1 -type d | sort | head -n "$((anzahl - behalten))" \
            | while read -r alt; do rm -rf -- "$alt"; done
    fi
}
aufraeumen daily "$KEEP_DAILY"
aufraeumen weekly "$KEEP_WEEKLY"

# ─── 6. Abschluss ─────────────────────────────────────────────────────────────
SCHRITT="Abschluss"
log "=== Sicherung fertig: $(du -sh "$BACKUP_PATH" | cut -f1), ${VOLUME_ANZAHL} Volume(s) ==="
melde ok
