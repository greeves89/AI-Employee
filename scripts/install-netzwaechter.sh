#!/usr/bin/env bash
# install-netzwaechter.sh - Netzwächter als systemd-Dienst einrichten
#
# Der Netzwächter (scripts/netzwaechter.py) sperrt Agenten und Agenten-Apps gegen
# Heimnetz/Firmennetz, Router und den Host selbst. Internet und Plattform bleiben
# erreichbar. Er läuft auf dem Host, weil er iptables braucht.
#
# Usage:
#   sudo ./scripts/install-netzwaechter.sh            # einrichten und starten
#   sudo ./scripts/install-netzwaechter.sh --zeigen   # nur anzeigen, was er sperren würde
#   sudo ./scripts/install-netzwaechter.sh --entfernen
#
# Ausnahmen (z. B. ein SAP-Server im Firmennetz, den Agenten brauchen):
#   /etc/ai-employee/netz-ausnahmen — ein Netz je Zeile (192.168.1.50 oder 10.0.5.0/24),
#   danach: sudo systemctl restart ai-employee-netzwaechter

set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT=/etc/systemd/system/ai-employee-netzwaechter.service

case "${1:-}" in
    --zeigen)
        exec python3 "${INSTALL_DIR}/scripts/netzwaechter.py" --zeigen ;;
    --entfernen)
        systemctl disable --now ai-employee-netzwaechter 2>/dev/null || true
        rm -f "$UNIT" /etc/modules-load.d/ai-employee-netzwaechter.conf /etc/sysctl.d/90-ai-employee-netzwaechter.conf
        systemctl daemon-reload
        python3 "${INSTALL_DIR}/scripts/netzwaechter.py" --entfernen
        exit 0 ;;
    "") ;;
    *) echo "Unbekannte Option: $1"; exit 1 ;;
esac

command -v iptables-restore >/dev/null || { echo "FEHLER: iptables-restore fehlt."; exit 1; }
mkdir -p /etc/ai-employee
[ -f /etc/ai-employee/netz-ausnahmen ] || cat > /etc/ai-employee/netz-ausnahmen <<'TXT'
# Ziele im Firmen- oder Heimnetz, die Agenten und ihre Apps erreichen dürfen.
# Ein Netz je Zeile, z. B.:
#   192.168.1.50        # SAP-Server
#   10.0.5.0/24         # Testumgebung
TXT

# Verkehr innerhalb einer Docker-Bridge läuft nur mit br_netfilter durch iptables.
echo br_netfilter > /etc/modules-load.d/ai-employee-netzwaechter.conf
echo "net.bridge.bridge-nf-call-iptables = 1" > /etc/sysctl.d/90-ai-employee-netzwaechter.conf
modprobe br_netfilter
echo 1 > /proc/sys/net/bridge/bridge-nf-call-iptables

sed "s#@INSTALL_DIR@#${INSTALL_DIR}#g" "${INSTALL_DIR}/deploy/ai-employee-netzwaechter.service" > "$UNIT"
systemctl daemon-reload
systemctl enable --now ai-employee-netzwaechter
sleep 3
systemctl --no-pager --lines=5 status ai-employee-netzwaechter || true
