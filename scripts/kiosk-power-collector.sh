#!/usr/bin/env bash
# AI-Employee kiosk host metrics + power collector (Raspberry Pi 5).
#
# Writes a JSON snapshot the orchestrator reads for /api/v1/kiosk/overview:
#   - power_w: real board power, summed from the Pi 5 PMIC rails (V*I)
#   - temp_c, cpu_percent, load, mem, disk, uptime
#   - today_kwh: accumulated energy since local midnight (integrates power over time)
#
# Runs as a small forever-loop under systemd (see kiosk-power.service). Output is
# written atomically so the reader never sees a half-written file.
set -u

OUT="${KIOSK_METRICS_OUT:-/var/lib/kiosk-metrics/metrics.json}"
STATE="${KIOSK_METRICS_STATE:-/var/lib/kiosk-metrics/energy-state}"
INTERVAL="${KIOSK_METRICS_INTERVAL:-3}"
mkdir -p "$(dirname "$OUT")"

# Sum real power across all PMIC rails that expose both a current (_A) and a
# voltage (_V) reading: power = sum(current_rail * voltage_rail).
read_power() {
  vcgencmd pmic_read_adc 2>/dev/null | awk '
    { n=$1; split($0, p, "="); val=p[2]+0
      t=substr(n, length(n), 1); rail=substr(n, 1, length(n)-2)
      if (t=="A") cur[rail]=val; else if (t=="V") volt[rail]=val }
    END { tot=0; for (r in cur) if (r in volt) tot+=cur[r]*volt[r]; printf "%.3f", tot }'
}

# CPU utilisation from /proc/stat deltas between iterations.
prev_total=0; prev_idle=0
cpu_percent() {
  local cpu u n s i w irq sirq steal
  read -r cpu u n s i w irq sirq steal _ < /proc/stat
  local idle=$((i + w))
  local total=$((u + n + s + i + w + irq + sirq + steal))
  local dt=$((total - prev_total)); local di=$((idle - prev_idle))
  prev_total=$total; prev_idle=$idle
  if [ "$dt" -gt 0 ]; then
    awk -v dt="$dt" -v di="$di" 'BEGIN{printf "%.1f", (dt-di)*100.0/dt}'
  else
    echo "0.0"
  fi
}
cpu_percent >/dev/null   # prime the baseline

today() { date +%F; }
acc_date=""; acc_kwh=0
if [ -f "$STATE" ]; then read -r acc_date acc_kwh < "$STATE" 2>/dev/null || true; fi
[ "$acc_date" = "$(today)" ] || { acc_date="$(today)"; acc_kwh=0; }

while true; do
  W=$(read_power); [ -z "$W" ] && W=0
  CPU=$(cpu_percent)
  TEMP=$(awk '{printf "%.1f", $1/1000}' /sys/class/thermal/thermal_zone0/temp 2>/dev/null)
  read -r l1 l2 l3 _ < /proc/loadavg
  MEMT=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)
  MEMA=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
  MEMU=$((MEMT - MEMA))
  read -r DT DU < <(df -BG / | tail -1 | awk '{gsub(/G/,"",$2); gsub(/G/,"",$3); print $2, $3}')
  UP=$(awk '{print int($1)}' /proc/uptime)

  # Reset the daily energy accumulator at local midnight.
  [ "$acc_date" = "$(today)" ] || { acc_date="$(today)"; acc_kwh=0; }
  acc_kwh=$(awk -v k="$acc_kwh" -v w="$W" -v dt="$INTERVAL" \
    'BEGIN{printf "%.6f", k + w*dt/3600.0/1000.0}')
  printf '%s %s\n' "$acc_date" "$acc_kwh" > "$STATE"

  tmp="$OUT.tmp"
  cat > "$tmp" <<JSON
{"ts": $(date +%s), "power_w": ${W:-0}, "temp_c": ${TEMP:-null}, "cpu_percent": ${CPU:-0.0},
 "load": [${l1:-0}, ${l2:-0}, ${l3:-0}],
 "mem_used_mb": ${MEMU:-0}, "mem_total_mb": ${MEMT:-0},
 "disk_used_gb": ${DU:-0}, "disk_total_gb": ${DT:-0},
 "uptime_s": ${UP:-0}, "today_kwh": ${acc_kwh:-0}}
JSON
  mv "$tmp" "$OUT"
  sleep "$INTERVAL"
done
