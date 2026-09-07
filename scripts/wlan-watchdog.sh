#!/usr/bin/env bash
# WLAN-Watchdog fuer einen Pi ohne Tastatur und Monitor.
#
# Ein Raspberry Pi verliert gelegentlich still das WLAN (haengender Treiber,
# eingefrorener Netzwerk-Stack): Er laeuft weiter, ist aber von aussen weg —
# Tunnel, App und SSH sind tot, und bemerkt wird es erst Stunden spaeter.
# Ohne Tastatur bleibt dann nur der Stecker. Dieses Skript holt das Geraet
# selbst zurueck: Router anpingen; scheitert das, WLAN neu verbinden; scheitert
# es mehrfach in Folge, neu starten. Laeuft ueber den zugehoerigen systemd-Timer.
#
# Gateway und Schnittstelle werden ermittelt, nicht eingetragen — das Skript
# soll auf jedem Geraet laufen, nicht nur auf einem bestimmten.
#
# Zum Pruefen ohne Eingriff: WLAN_WATCHDOG_DRY=1 (nur protokollieren).
# Zum Pruefen des Fehlerpfads: WLAN_WATCHDOG_GATEWAY=<unerreichbare Adresse>.
set -u

ZAEHLER=/run/wlan-watchdog.fails
MAX_FEHLSCHLAEGE=${WLAN_WATCHDOG_MAX:-3}
TROCKEN=${WLAN_WATCHDOG_DRY:-0}

GW=${WLAN_WATCHDOG_GATEWAY:-$(ip route show default 2>/dev/null | awk '/default/{print $3; exit}')}
DEV=$(ip route show default 2>/dev/null | awk '/default/{print $5; exit}')
# Ohne Standardroute ist das Netz komplett weg — dann die WLAN-Schnittstelle
# beim Namen nehmen, die NetworkManager kennt.
if [ -z "$DEV" ]; then
    DEV=$(nmcli -t -f DEVICE,TYPE device 2>/dev/null | awk -F: '$2=="wifi"{print $1; exit}')
fi

if [ -n "$GW" ] && ping -c 2 -W 3 "$GW" >/dev/null 2>&1; then
    rm -f "$ZAEHLER"
    exit 0
fi

n=$(( $(cat "$ZAEHLER" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$ZAEHLER"
logger -t wlan-watchdog "Gateway ${GW:-unbekannt} nicht erreichbar (Fehlschlag $n/$MAX_FEHLSCHLAEGE)"

if [ "$n" -ge "$MAX_FEHLSCHLAEGE" ]; then
    logger -t wlan-watchdog "Nach $n Fehlschlaegen in Folge: Neustart"
    rm -f "$ZAEHLER"
    [ "$TROCKEN" = "1" ] || systemctl reboot
    exit 0
fi

logger -t wlan-watchdog "Verbinde ${DEV:-wlan?} neu"
if [ "$TROCKEN" != "1" ] && [ -n "$DEV" ]; then
    nmcli device disconnect "$DEV" >/dev/null 2>&1
    sleep 3
    nmcli device connect "$DEV" >/dev/null 2>&1
fi
