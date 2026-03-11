#!/usr/bin/env bash
# install-backup-cron.sh - Install automated backup schedule
#
# Installs a daily backup cron job that runs at 02:00 AM.
# Can optionally install a systemd timer instead (more reliable).
#
# Usage:
#   sudo ./scripts/install-backup-cron.sh [--systemd] [--backup-dir /path/to/backups]

set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/ai-employee}"
USE_SYSTEMD=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --systemd) USE_SYSTEMD=true; shift ;;
        --backup-dir) BACKUP_DIR="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

mkdir -p "$BACKUP_DIR"

if $USE_SYSTEMD; then
    # ── Systemd Timer (preferred) ──────────────────────────────────────────────

    cat > /etc/systemd/system/ai-employee-backup.service << EOF
[Unit]
Description=AI Employee Platform Backup
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
User=root
WorkingDirectory=${INSTALL_DIR}
Environment=BACKUP_DIR=${BACKUP_DIR}
ExecStart=${INSTALL_DIR}/scripts/backup.sh
StandardOutput=journal
StandardError=journal
SyslogIdentifier=ai-employee-backup
EOF

    cat > /etc/systemd/system/ai-employee-backup.timer << EOF
[Unit]
Description=Daily AI Employee Platform Backup
Requires=ai-employee-backup.service

[Timer]
OnCalendar=daily
RandomizedDelaySec=300
Persistent=true

[Install]
WantedBy=timers.target
EOF

    systemctl daemon-reload
    systemctl enable --now ai-employee-backup.timer

    echo "Systemd timer installed and enabled."
    echo "Check status: systemctl status ai-employee-backup.timer"
    echo "Run manually: systemctl start ai-employee-backup.service"

else
    # ── Cron Job (fallback) ────────────────────────────────────────────────────

    CRON_CMD="0 2 * * * cd ${INSTALL_DIR} && BACKUP_DIR=${BACKUP_DIR} ./scripts/backup.sh >> ${BACKUP_DIR}/backup.log 2>&1"

    # Remove old entry if exists
    crontab -l 2>/dev/null | grep -v "ai-employee.*backup" | crontab - || true

    # Add new entry
    (crontab -l 2>/dev/null; echo "$CRON_CMD") | crontab -

    echo "Cron job installed: runs daily at 02:00 AM"
    echo "Check with: crontab -l"
fi

echo ""
echo "Backup directory: ${BACKUP_DIR}"
echo "Test with: ${INSTALL_DIR}/scripts/backup.sh"
