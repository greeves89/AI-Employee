#!/usr/bin/env bash
# install-backup-cron.sh - Install automated backup schedule
#
# Installs a daily backup cron job that runs at 02:00 AM.
# Can optionally install a systemd timer instead (more reliable).
#
# Usage:
#   sudo ./scripts/install-backup-cron.sh [--systemd] [--backup-dir /path/to/backups]
#        [--zeiten "02:30,14:30"] [--mount /mnt/nas] [--keep-daily 14]
#
#   --zeiten      Uhrzeiten (systemd: mehrere möglich, Standard: einmal täglich)
#   --mount       Mountpunkt, der eingehängt sein muss (NAS). backup.sh bricht
#                 sonst ab, statt auf die Platte der Anlage zu schreiben.
#   --keep-daily  Aufbewahrung der täglichen Sicherungen (bei zwei Läufen am Tag
#                 entsprechend verdoppeln)
#
# Die Sicherung läuft mit niedrigster CPU- und Platten-Priorität: Sie darf der
# Anlage nicht die Maschine wegnehmen.

set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/ai-employee}"
USE_SYSTEMD=false
ZEITEN=""
MOUNT=""
KEEP_DAILY=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --systemd) USE_SYSTEMD=true; shift ;;
        --backup-dir) BACKUP_DIR="$2"; shift 2 ;;
        --zeiten) ZEITEN="$2"; shift 2 ;;
        --mount) MOUNT="$2"; shift 2 ;;
        --keep-daily) KEEP_DAILY="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# Mit --mount: das Ziel erst anlegen, wenn das Laufwerk wirklich da ist.
if [ -n "$MOUNT" ]; then
    # shellcheck source=lib/sicherung.sh
    . "${INSTALL_DIR}/scripts/lib/sicherung.sh"
    ziel_eingehaengt "$MOUNT" || { echo "FEHLER: ${MOUNT} ist nicht eingehängt."; exit 1; }
fi
mkdir -p "$BACKUP_DIR"

UMGEBUNG="Environment=BACKUP_DIR=${BACKUP_DIR}"
[ -n "$MOUNT" ] && UMGEBUNG="${UMGEBUNG}
Environment=BACKUP_MOUNT=${MOUNT}"
[ -n "$KEEP_DAILY" ] && UMGEBUNG="${UMGEBUNG}
Environment=BACKUP_KEEP_DAILY=${KEEP_DAILY}"

if $USE_SYSTEMD; then
    KALENDER="OnCalendar=daily"
    if [ -n "$ZEITEN" ]; then
        KALENDER=""
        for zeit in ${ZEITEN//,/ }; do
            KALENDER="${KALENDER}OnCalendar=*-*-* ${zeit}:00
"
        done
    fi
    ABHAENGIG=""
    [ -n "$MOUNT" ] && ABHAENGIG="RequiresMountsFor=${MOUNT}"

    # ── Systemd Timer (preferred) ──────────────────────────────────────────────

    cat > /etc/systemd/system/ai-employee-backup.service << EOF
[Unit]
Description=AI Employee Platform Backup
After=docker.service
Requires=docker.service
${ABHAENGIG}

[Service]
Type=oneshot
User=root
WorkingDirectory=${INSTALL_DIR}
${UMGEBUNG}
Nice=19
IOSchedulingClass=idle
ExecStart=${INSTALL_DIR}/scripts/backup.sh
StandardOutput=journal
StandardError=journal
SyslogIdentifier=ai-employee-backup
EOF

    cat > /etc/systemd/system/ai-employee-backup.timer << EOF
[Unit]
Description=Daily AI Employee Platform Backup

[Timer]
${KALENDER}RandomizedDelaySec=300
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

    [ -n "$ZEITEN" ] && { echo "--zeiten gibt es nur mit --systemd"; exit 1; }
    VARS="BACKUP_DIR=${BACKUP_DIR}"
    [ -n "$MOUNT" ] && VARS="${VARS} BACKUP_MOUNT=${MOUNT}"
    [ -n "$KEEP_DAILY" ] && VARS="${VARS} BACKUP_KEEP_DAILY=${KEEP_DAILY}"
    CRON_CMD="0 2 * * * cd ${INSTALL_DIR} && ${VARS} nice -n 19 ./scripts/backup.sh >> /var/log/ai-employee-backup.log 2>&1"

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
