#!/usr/bin/env bash
# restore.sh - Restore AI Employee Platform from backup
#
# Usage:
#   ./scripts/restore.sh --backup /var/backups/ai-employee/daily/20260219_120000
#
# Options:
#   --backup PATH     Path to backup directory (required)
#   --db-only         Only restore the database (skip volumes)
#   --dry-run         Show what would be restored without doing it
#
# Environment variables:
#   DB_PASSWORD       PostgreSQL password
#   POSTGRES_USER     PostgreSQL user (default: postgres)
#   POSTGRES_DB       PostgreSQL database name (default: ai_employee)
#   COMPOSE_PROJECT   Docker compose project name (default: ai-employee)
#
# IMPORTANT: Stop the application before restoring volumes:
#   docker compose stop orchestrator frontend agent
#   Then run this script, then restart:
#   docker compose start orchestrator frontend agent

set -euo pipefail

POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-ai_employee}"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-ai-employee}"

BACKUP_PATH=""
DB_ONLY=false
DRY_RUN=false

# ─── Argument Parsing ──────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --backup) BACKUP_PATH="$2"; shift 2 ;;
        --db-only) DB_ONLY=true; shift ;;
        --dry-run) DRY_RUN=true; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

[ -z "$BACKUP_PATH" ] && { echo "Error: --backup PATH is required"; exit 1; }
[ -d "$BACKUP_PATH" ] || { echo "Error: Backup directory not found: $BACKUP_PATH"; exit 1; }

# ─── Helper Functions ──────────────────────────────────────────────────────────

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

run() {
    if $DRY_RUN; then
        echo "[DRY RUN] $*"
    else
        "$@"
    fi
}

die() {
    log "ERROR: $*"
    exit 1
}

# ─── Verify Manifest ───────────────────────────────────────────────────────────

MANIFEST="${BACKUP_PATH}/MANIFEST"
if [ -f "$MANIFEST" ]; then
    log "Reading backup manifest:"
    grep -E "^(timestamp|backup_type|hostname)=" "$MANIFEST" | while IFS= read -r line; do
        log "  $line"
    done

    log "Verifying checksums..."
    if ! $DRY_RUN; then
        # Verify each file's checksum
        grep "sha256" "$MANIFEST" 2>/dev/null | while IFS= read -r checksum_line; do
            sha256sum -c <(echo "$checksum_line") >/dev/null 2>&1 || \
                log "WARNING: Checksum mismatch for: $checksum_line"
        done || true
        log "Checksum verification complete"
    fi
fi

# ─── Locate Backup Files ───────────────────────────────────────────────────────

DB_FILE=$(find "$BACKUP_PATH" -name "postgres_*.sql.gz" | head -1)
[ -z "$DB_FILE" ] && die "No database backup found in $BACKUP_PATH"
log "Database backup: $DB_FILE"

# ─── 1. Database Restore ───────────────────────────────────────────────────────

log "=== Restoring PostgreSQL database '${POSTGRES_DB}' ==="

PG_CONTAINER=$(docker ps --filter "name=${COMPOSE_PROJECT}-postgres" --format "{{.Names}}" | head -1)
[ -z "$PG_CONTAINER" ] && die "PostgreSQL container not found. Start postgres first: docker compose start postgres"

log "Dropping and recreating database (existing data will be lost)..."
run docker exec -e "PGPASSWORD=${DB_PASSWORD:-postgres}" "$PG_CONTAINER" \
    psql -U "$POSTGRES_USER" -c "DROP DATABASE IF EXISTS ${POSTGRES_DB};"
run docker exec -e "PGPASSWORD=${DB_PASSWORD:-postgres}" "$PG_CONTAINER" \
    psql -U "$POSTGRES_USER" -c "CREATE DATABASE ${POSTGRES_DB};"

log "Restoring database from backup..."
run sh -c "zcat '${DB_FILE}' | PGPASSWORD='${DB_PASSWORD:-postgres}' \
    docker exec -i '${PG_CONTAINER}' \
    psql -U '${POSTGRES_USER}' '${POSTGRES_DB}'"

log "Database restore complete."

# ─── 2. Volume Restore ─────────────────────────────────────────────────────────

if $DB_ONLY; then
    log "Skipping volume restore (--db-only specified)"
else
    restore_volume() {
        local volume_name="$1"
        local backup_file
        backup_file=$(find "$BACKUP_PATH" -name "${volume_name}_*.tar.gz" | head -1)

        if [ -z "$backup_file" ]; then
            log "WARNING: No backup found for volume $volume_name, skipping"
            return
        fi

        local full_volume="${COMPOSE_PROJECT}_${volume_name}"
        log "Restoring volume: $full_volume from $(basename "$backup_file")..."

        # Ensure volume exists
        run docker volume create "$full_volume" >/dev/null 2>&1 || true

        # Clear and restore
        run docker run --rm \
            -v "${full_volume}:/data" \
            -v "${BACKUP_PATH}:/backup:ro" \
            alpine:3.21 \
            sh -c "rm -rf /data/* && tar xzf '/backup/$(basename "$backup_file")' -C /data"

        log "Volume $full_volume restored."
    }

    log "=== Restoring Docker volumes ==="
    log "WARNING: Ensure orchestrator, frontend, and agent are stopped before restoring volumes!"
    log "         docker compose stop orchestrator frontend agent"

    restore_volume "postgres_data"
    restore_volume "redis_data"
    restore_volume "letsencrypt_data"
fi

# ─── 3. Post-Restore Validation ────────────────────────────────────────────────

if ! $DRY_RUN && ! $DB_ONLY; then
    log "=== Post-restore validation ==="

    # Check DB is accessible
    TABLE_COUNT=$(PGPASSWORD="${DB_PASSWORD:-postgres}" docker exec "$PG_CONTAINER" \
        psql -U "$POSTGRES_USER" "$POSTGRES_DB" -t -c \
        "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" 2>/dev/null | tr -d ' \n' || echo "0")

    log "Database tables restored: $TABLE_COUNT"
fi

log "=== Restore complete ==="
log ""
log "Next steps:"
log "  1. Start services: docker compose start orchestrator frontend agent"
log "  2. Run migrations: docker compose exec orchestrator alembic upgrade head"
log "  3. Verify health:  curl http://localhost:8000/health"
