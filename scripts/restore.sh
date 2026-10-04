#!/usr/bin/env bash
# restore.sh — Sicherung der AI-Employee-Anlage zurückspielen (#892)
#
# Gegenstück zu scripts/backup.sh, mit derselben Volume-Liste
# (scripts/lib/sicherung.sh). Reihenfolge:
#
#   1. Prüfsummen der Sicherung kontrollieren
#   2. Orchestrator, Oberfläche, Redis und alle Agenten anhalten
#   3. SCHLÜSSEL ZUERST: .env und orchestrator/data/ zurücklegen — ohne den
#      passenden ENCRYPTION_KEY wären alle Geheimnisse danach unlesbar
#   4. Datenbank aus dem pg_dump — vorher (gleich nach dem Anhalten) eine
#      Sicherheitskopie der bisherigen Datenbank; scheitert sie, Abbruch
#   5. Volumes (Arbeitsordner, Sitzungen, gemeinsamer Ordner, Redis …)
#   6. Anlage starten und Selbsttest: ist ein gespeichertes Geheimnis mit dem
#      zurückgelegten Schlüssel lesbar?
#
# Erkennt das alte Sicherungsformat (vor #892: nur Datenbank und Redis, kein
# Schlüssel, keine Arbeitsordner) und spielt zurück, was darin ist.
#
# Aufruf:
#   ./scripts/restore.sh --backup /var/backups/ai-employee/daily/20261004_020000
#
# Optionen:
#   --backup PFAD   Sicherungsordner (Pflicht)
#   --db-only       nur die Datenbank
#   --dry-run       nur anzeigen, was passieren würde
#   --yes           ohne Rückfrage (für Skripte)
#
# Umgebungsvariablen: COMPOSE_PROJECT_NAME, PG_CONTAINER, POSTGRES_USER, POSTGRES_DB,
#   BACKUP_DIR (Ziel der Sicherheitskopie der bisherigen Datenbank,
#   Standard /var/backups/ai-employee — Unterordner vor-wiederherstellung-<zeit>)

set -euo pipefail
umask 077

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/sicherung.sh
. "${INSTALL_DIR}/scripts/lib/sicherung.sh"

PG_CONTAINER="${PG_CONTAINER:-ai-employee-postgres}"
ORCH_CONTAINER="${ORCH_CONTAINER:-ai-employee-orchestrator}"
POSTGRES_USER="${POSTGRES_USER:-ai_employee}"
POSTGRES_DB="${POSTGRES_DB:-ai_employee}"
ALPINE_IMAGE="${ALPINE_IMAGE:-alpine:3.21}"
# Hierhin kommt vor dem Rückspielen die Sicherheitskopie der BISHERIGEN
# Datenbank (derselbe Standard wie in backup.sh).
BACKUP_DIR="${BACKUP_DIR:-/var/backups/ai-employee}"

BACKUP_PATH=""
DB_ONLY=false
DRY_RUN=false
JA=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --backup) BACKUP_PATH="$2"; shift 2 ;;
        --db-only) DB_ONLY=true; shift ;;
        --dry-run) DRY_RUN=true; shift ;;
        --yes) JA=true; shift ;;
        *) echo "Unbekannte Option: $1"; exit 1 ;;
    esac
done

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
die() { log "FEHLER: $*"; exit 1; }
run() {
    if $DRY_RUN; then
        echo "[PROBELAUF] $*"
    else
        "$@"
    fi
}

[ -n "$BACKUP_PATH" ] || die "--backup PFAD fehlt"
[ -d "$BACKUP_PATH" ] || die "Sicherungsordner nicht gefunden: $BACKUP_PATH"
BACKUP_PATH="$(cd "$BACKUP_PATH" && pwd)"
command -v docker >/dev/null 2>&1 || die "docker nicht gefunden"
cd "$INSTALL_DIR"

# ─── Format erkennen ──────────────────────────────────────────────────────────
MANIFEST="${BACKUP_PATH}/MANIFEST"
FORMAT=1
if [ -f "$MANIFEST" ] && grep -qx "format=2" "$MANIFEST"; then
    FORMAT=2
fi

if [ "$FORMAT" = "2" ]; then
    DB_FILE="${BACKUP_PATH}/postgres.sql.gz"
    log "Sicherung (Format 2):"
    grep -E "^(timestamp|version|hostname|compose_projekt|volumes)=" "$MANIFEST" | sed 's/^/    /'
else
    DB_FILE=$(find "$BACKUP_PATH" -maxdepth 1 -name "postgres_*.sql.gz" | head -n 1)
    log "ALTES Sicherungsformat erkannt (vor #892)."
    log "  Enthalten sind nur Datenbank und Redis — KEINE Arbeitsordner der Agenten,"
    log "  KEIN Verschlüsselungsschlüssel. .env und orchestrator/data/ dieser Anlage"
    log "  bleiben, wie sie sind; Geheimnisse sind nur lesbar, wenn hier noch derselbe"
    log "  Schlüssel liegt wie zum Zeitpunkt der Sicherung."
fi
[ -n "$DB_FILE" ] && [ -f "$DB_FILE" ] || die "Keine Datenbanksicherung in $BACKUP_PATH"

# ─── 1. Prüfsummen ────────────────────────────────────────────────────────────
if [ "$FORMAT" = "2" ]; then
    log "Prüfe Prüfsummen …"
    if ! (cd "$BACKUP_PATH" && grep -E '^[0-9a-f]{64}  ' MANIFEST | pruefsumme -c - >/dev/null); then
        die "Prüfsummen stimmen nicht — Sicherung beschädigt oder unvollständig."
    fi
    log "Prüfsummen in Ordnung."
fi

# ─── Rückfrage ────────────────────────────────────────────────────────────────
if ! $DRY_RUN && ! $JA; then
    echo ""
    echo "ACHTUNG: Die aktuellen Daten dieser Anlage werden durch die Sicherung ersetzt"
    echo "         (Datenbank$($DB_ONLY || echo ", Arbeitsordner, Konfiguration"))."
    echo "         Die bisherige Datenbank wird vorher nach ${BACKUP_DIR}/vor-wiederherstellung-…"
    echo "         gesichert$($DB_ONLY || echo "; Arbeitsordner werden ohne Kopie ersetzt")."
    printf "Weiter? Bitte 'ja' eingeben: "
    read -r antwort
    [ "$antwort" = "ja" ] || die "Abgebrochen."
fi

[ -n "$(docker ps -q --filter "name=^${PG_CONTAINER}\$")" ] \
    || die "Postgres-Container ${PG_CONTAINER} läuft nicht — zuerst: docker compose up -d postgres"
PROJEKT=$(compose_projekt "$INSTALL_DIR")
log "Compose-Projekt: ${PROJEKT}"

# ─── 2. Anhalten ──────────────────────────────────────────────────────────────
log "Halte Orchestrator, Oberfläche und Agenten an …"
run docker compose stop orchestrator frontend
AGENTEN=$(docker ps -q --filter "label=ai-employee.type=agent" || true)
if [ -n "$AGENTEN" ]; then
    # shellcheck disable=SC2086
    run docker stop $AGENTEN >/dev/null
fi

# ─── Sicherheitskopie der bisherigen Datenbank ────────────────────────────────
# Weiter unten wird die laufende Datenbank per DROP verworfen. War die gewählte
# Sicherung die falsche (Tippfehler im Pfad, zu alt), wären die aktuellen Daten
# sonst endgültig fort. Scheitert die Kopie, wird abgebrochen — noch bevor
# Schlüssel, Passwort oder Datenbank angefasst sind.
VORHER_ZEIT=$(date +%Y%m%d_%H%M%S)
VORHER_ORDNER="${BACKUP_DIR}/vor-wiederherstellung-${VORHER_ZEIT}"
# Name im alten Sicherungsformat: so spielt `restore.sh --backup <ordner>
# --db-only` die Kopie ohne Umweg wieder zurück.
VORHER_DATEI="${VORHER_ORDNER}/postgres_${VORHER_ZEIT}.sql.gz"
DB_VORHANDEN=$(docker exec -i "$PG_CONTAINER" psql -tA -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres \
    -c "SELECT 1 FROM pg_database WHERE datname = '${POSTGRES_DB}';") \
    || die "Postgres antwortet nicht — Wiederherstellung abgebrochen, nichts verändert."
if [ "$(echo "$DB_VORHANDEN" | tr -d '[:space:]')" != "1" ]; then
    log "Datenbank ${POSTGRES_DB} gibt es noch nicht — keine Sicherheitskopie nötig."
    VORHER_DATEI=""
elif $DRY_RUN; then
    echo "[PROBELAUF] pg_dump ${POSTGRES_DB} | gzip > ${VORHER_DATEI}"
else
    log "Sichere die bisherige Datenbank nach ${VORHER_ORDNER} …"
    mkdir -p "$VORHER_ORDNER" || die "Ordner ${VORHER_ORDNER} nicht anlegbar — nichts verändert."
    chmod 700 "$VORHER_ORDNER"
    if ! docker exec "$PG_CONTAINER" pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" \
            | gzip > "$VORHER_DATEI"; then
        rm -f "$VORHER_DATEI"
        die "Sicherheitskopie der bisherigen Datenbank gescheitert — Wiederherstellung abgebrochen, nichts verändert. Anlage wieder starten: docker compose up -d"
    fi
    chmod 600 "$VORHER_DATEI"
    log "Bisherige Datenbank gesichert: ${VORHER_DATEI}"
    log "  Zurück zum bisherigen Stand: ./scripts/restore.sh --backup ${VORHER_ORDNER} --db-only"
fi

# ─── 3. Schlüssel zuerst ──────────────────────────────────────────────────────
if [ "$FORMAT" = "2" ] && ! $DB_ONLY; then
    log "=== Konfiguration und Schlüssel ==="
    [ -f "${BACKUP_PATH}/konfiguration.tar.gz" ] || die "konfiguration.tar.gz fehlt in der Sicherung"
    ALTES_DB_PW=$(env_wert .env DB_PASSWORD)
    if [ -f .env ]; then
        SICHERHEITSKOPIE=".env.vor-wiederherstellung-$(date +%Y%m%d_%H%M%S)"
        run cp -p .env "$SICHERHEITSKOPIE"
        log "Bisherige .env aufgehoben als ${SICHERHEITSKOPIE}"
    fi
    run konfiguration_auspacken "${BACKUP_PATH}/konfiguration.tar.gz" "$INSTALL_DIR" "$ALPINE_IMAGE"
    run chmod 600 .env
    log "Schlüssel und .env zurückgelegt."

    # Das Datenbank-Passwort steht in der .env, gilt aber für die Rolle in der
    # LAUFENDEN Datenbank. Kommt die Sicherung von einer anderen Anlage, käme der
    # Orchestrator sonst nicht mehr hinein.
    NEUES_DB_PW=$(env_wert .env DB_PASSWORD)
    if ! $DRY_RUN && [ -n "$NEUES_DB_PW" ] && [ "$NEUES_DB_PW" != "$ALTES_DB_PW" ]; then
        log "Datenbank-Passwort aus der Sicherung übernehmen …"
        echo "ALTER USER ${POSTGRES_USER} WITH PASSWORD :'pw';" \
            | docker exec -i "$PG_CONTAINER" psql -q -v ON_ERROR_STOP=1 -v pw="$NEUES_DB_PW" \
                -U "$POSTGRES_USER" -d postgres >/dev/null
    fi
fi

# ─── 4. Datenbank ─────────────────────────────────────────────────────────────
log "=== Datenbank ${POSTGRES_DB} ==="
psql_admin() {
    run docker exec -i "$PG_CONTAINER" psql -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres -c "$1"
}
psql_admin "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${POSTGRES_DB}' AND pid <> pg_backend_pid();" >/dev/null
psql_admin "DROP DATABASE IF EXISTS ${POSTGRES_DB};"
psql_admin "CREATE DATABASE ${POSTGRES_DB} OWNER ${POSTGRES_USER};"
if $DRY_RUN; then
    echo "[PROBELAUF] gunzip -c ${DB_FILE} | psql ${POSTGRES_DB}"
else
    gunzip -c "$DB_FILE" | docker exec -i "$PG_CONTAINER" \
        psql -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null \
        || die "Datenbank ließ sich nicht zurückspielen (siehe Meldung oben). Die Sicherung ist unverändert — nach Behebung erneut aufrufen."
fi
log "Datenbank zurückgespielt."

# ─── 5. Volumes ───────────────────────────────────────────────────────────────
volume_zurueckspielen() {
    local datei="$1" volume="$2" ordner
    ordner=$(dirname "$datei")
    log "Volume ${volume} …"
    run docker volume create "$volume" >/dev/null
    run docker run --rm \
        -v "${volume}:/data" \
        -v "${ordner}:/backup:ro" \
        "$ALPINE_IMAGE" \
        sh -c "find /data -mindepth 1 -delete && tar xzf '/backup/$(basename "$datei")' -C /data"
}

ANZAHL=0
if ! $DB_ONLY; then
    log "=== Volumes ==="
    run docker compose stop redis
    if [ "$FORMAT" = "2" ]; then
        for datei in "${BACKUP_PATH}"/volumes/*.tar.gz; do
            [ -f "$datei" ] || continue
            archiv=$(basename "$datei" .tar.gz)
            # Dieselbe Namensprüfung wie beim Sichern — ein manipulierter
            # Dateiname darf kein beliebiges Volume treffen.
            ziel=$(volume_fuer_archiv "$PROJEKT" "$archiv")
            if [ -z "$(echo "$ziel" | volumes_auswaehlen "$PROJEKT")" ]; then
                log "WARNUNG: ${archiv} gehört nicht zur Sicherungsliste — übersprungen."
                continue
            fi
            volume_zurueckspielen "$datei" "$ziel"
            ANZAHL=$((ANZAHL + 1))
        done
    else
        # Altes Format: <volume>_<zeitstempel>.tar.gz. Die rohen Postgres-Dateien
        # werden NICHT zurückgespielt — die Datenbank kam eben aus dem Dump.
        datei=$(find "$BACKUP_PATH" -maxdepth 1 -name "redis_data_*.tar.gz" | head -n 1)
        if [ -n "$datei" ]; then
            volume_zurueckspielen "$datei" "${PROJEKT}_redis_data"
            ANZAHL=1
        fi
    fi
    log "${ANZAHL} Volume(s) zurückgespielt."
fi

# ─── 6. Starten und Selbsttest ────────────────────────────────────────────────
log "=== Anlage starten ==="
run docker compose up -d
if $DRY_RUN; then
    log "Probelauf beendet — nichts verändert."
    exit 0
fi

PORT=$(env_wert .env ORCHESTRATOR_PORT | grep -E '^[0-9]+$' || echo 8000)
VERSUCHE=60
until curl -sf "http://127.0.0.1:${PORT}/api/v1/health" >/dev/null 2>&1; do
    VERSUCHE=$((VERSUCHE - 1))
    [ "$VERSUCHE" -le 0 ] && die "Orchestrator startet nicht — docker compose logs orchestrator"
    sleep 3
done
log "Orchestrator läuft."

log "Selbsttest: sind gespeicherte Geheimnisse mit dem vorhandenen Schlüssel lesbar?"
if ERGEBNIS=$(docker exec "$ORCH_CONTAINER" python -m app.core.schluessel_selbsttest 2>/dev/null); then
    log "Selbsttest bestanden: ${ERGEBNIS}"
else
    log "SELBSTTEST GESCHEITERT: ${ERGEBNIS:-keine Ausgabe}"
    log "  Die Geheimnisse (KI-Konten, Keys, Zugänge) sind mit dem aktuellen Schlüssel"
    log "  NICHT lesbar. ENCRYPTION_KEY in .env und orchestrator/data/.encryption_key"
    log "  prüfen — NICHT neu erzeugen, sondern den Schlüssel der gesicherten Anlage"
    log "  eintragen und den Orchestrator neu starten."
    exit 2
fi

log "=== Wiederherstellung fertig ==="
[ -n "$VORHER_DATEI" ] && log "Die Datenbank vor der Wiederherstellung liegt in ${VORHER_DATEI}."
log "Agenten starten beim nächsten Auftrag von selbst. Gibt es auf dieser Anlage"
log "noch keine Container (neuer Rechner), in der Agentenliste je Agent die Aktion"
log "„Update“ ausführen (oder POST /agents/{id}/update) — die Arbeitsordner bleiben erhalten."
