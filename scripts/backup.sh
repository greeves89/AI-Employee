#!/usr/bin/env bash
# backup.sh - Automated backup for AI Employee Platform
#
# Backs up:
#   1. PostgreSQL database (pg_dump)
#   2. Docker volumes (postgres_data, redis_data, letsencrypt_data)
#
# Retention policy:
#   7 daily backups, 4 weekly backups (taken on Sunday)
#
# Usage:
#   ./scripts/backup.sh [--dest /path/to/backup/dir]
#
# Environment variables:
#   BACKUP_DIR        Override backup destination (default: /var/backups/ai-employee)
#   DB_PASSWORD       PostgreSQL password
#   POSTGRES_USER     PostgreSQL user (default: postgres)
#   POSTGRES_DB       PostgreSQL database name (default: ai_employee)
#   COMPOSE_PROJECT   Docker compose project name (default: ai-employee)

set -euo pipefail

# ─── Configuration ─────────────────────────────────────────────────────────────

BACKUP_DIR="${BACKUP_DIR:-/var/backups/ai-employee}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-ai_employee}"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-ai-employee}"
KEEP_DAILY=7
KEEP_WEEKLY=4

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DAY_OF_WEEK=$(date +%u)  # 1=Mon, 7=Sun
BACKUP_TYPE="daily"
[ "$DAY_OF_WEEK" = "7" ] && BACKUP_TYPE="weekly"

BACKUP_PATH="${BACKUP_DIR}/${BACKUP_TYPE}/${TIMESTAMP}"
LOG="${BACKUP_DIR}/backup.log"

# ─── Helper Functions ──────────────────────────────────────────────────────────

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"
}

die() {
    log "ERROR: $*"
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

# ─── Preflight Checks ──────────────────────────────────────────────────────────

require_command docker
require_command gzip

mkdir -p "${BACKUP_PATH}"
log "=== Backup started: type=${BACKUP_TYPE} dest=${BACKUP_PATH} ==="

# ─── 1. Database Backup ────────────────────────────────────────────────────────

log "Backing up PostgreSQL database '${POSTGRES_DB}'..."

DB_BACKUP_FILE="${BACKUP_PATH}/postgres_${POSTGRES_DB}_${TIMESTAMP}.sql.gz"

# Find the postgres container
PG_CONTAINER=$(docker ps --filter "name=${COMPOSE_PROJECT}-postgres" --format "{{.Names}}" | head -1)
[ -z "$PG_CONTAINER" ] && die "PostgreSQL container not found. Is the stack running?"

PGPASSWORD="${DB_PASSWORD:-postgres}" docker exec "$PG_CONTAINER" \
    pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" \
    | gzip > "$DB_BACKUP_FILE"

DB_SIZE=$(du -sh "$DB_BACKUP_FILE" | cut -f1)
log "Database backup complete: $DB_BACKUP_FILE ($DB_SIZE)"

# ─── 2. Volume Backups ─────────────────────────────────────────────────────────

backup_volume() {
    local volume_name="$1"
    local full_volume="${COMPOSE_PROJECT}_${volume_name}"
    local output_file="${BACKUP_PATH}/${volume_name}_${TIMESTAMP}.tar.gz"

    # Check if volume exists
    if ! docker volume inspect "$full_volume" >/dev/null 2>&1; then
        log "WARNING: Volume $full_volume not found, skipping"
        return
    fi

    log "Backing up volume: $full_volume..."
    docker run --rm \
        -v "${full_volume}:/data:ro" \
        -v "${BACKUP_PATH}:/backup" \
        alpine:3.21 \
        tar czf "/backup/$(basename "$output_file")" -C /data .

    local size
    size=$(du -sh "$output_file" | cut -f1)
    log "Volume backup complete: $output_file ($size)"
}

backup_volume "postgres_data"
backup_volume "redis_data"
backup_volume "letsencrypt_data"
backup_volume "prometheus_data"

# ─── 3. Manifest ───────────────────────────────────────────────────────────────

MANIFEST="${BACKUP_PATH}/MANIFEST"
{
    echo "timestamp=${TIMESTAMP}"
    echo "backup_type=${BACKUP_TYPE}"
    echo "postgres_db=${POSTGRES_DB}"
    echo "hostname=$(hostname)"
    find "$BACKUP_PATH" -type f ! -name MANIFEST -exec sha256sum {} \;
} > "$MANIFEST"

log "Manifest written: $MANIFEST"

# ─── 4. Retention Cleanup ──────────────────────────────────────────────────────

cleanup_old_backups() {
    local backup_type="$1"
    local keep="$2"
    local backup_base="${BACKUP_DIR}/${backup_type}"

    if [ ! -d "$backup_base" ]; then
        return
    fi

    # Count existing backups
    local count
    count=$(find "$backup_base" -maxdepth 1 -type d | wc -l)
    count=$((count - 1))  # subtract the base dir itself

    if [ "$count" -gt "$keep" ]; then
        local to_delete=$((count - keep))
        log "Removing $to_delete old ${backup_type} backup(s) (keeping ${keep})..."
        find "$backup_base" -maxdepth 1 -type d ! -path "$backup_base" \
            | sort | head -n "$to_delete" \
            | xargs rm -rf
    fi
}

cleanup_old_backups "daily" "$KEEP_DAILY"
cleanup_old_backups "weekly" "$KEEP_WEEKLY"

# ─── 5. Summary ────────────────────────────────────────────────────────────────

TOTAL_SIZE=$(du -sh "$BACKUP_PATH" | cut -f1)
log "=== Backup complete: $TOTAL_SIZE total ==="

# Optional: send success notification via orchestrator webhook
# curl -s -X POST "http://localhost:8000/api/v1/notifications" \
#     -H "Content-Type: application/json" \
#     -d "{\"message\": \"Backup complete: $BACKUP_PATH ($TOTAL_SIZE)\"}" || true
