#!/bin/bash
# Runs as root (before the container drops to the unprivileged "agent" user).
#
# Keeps the Claude Code / Codex CLIs current on every container start --
# clicking "Update" on an agent always recreates its container, so this is
# the update mechanism. Without it, the CLI version stays frozen at whatever
# was baked into the image at build time (bit us directly: Claude Code sat at
# 2.1.144 for a long time and silently couldn't offer the Claude 5 model
# family, independent of any credential/model-catalog fix).
#
# Best-effort: a failed/slow install must never block the agent from starting
# -- and, just as important, must never LEAVE IT WITHOUT A CLI.
set -eu

# Node bevorzugt von sich aus IPv6. Loest die Registry nur zu IPv6-Adressen
# auf und fehlt im Container die IPv6-Route, laeuft npm nicht in einen Fehler,
# sondern in einen HAENGER. Am 21.09.2026 stand ein Agent deshalb still: Das
# Startskript kam nie ueber diesen Aufruf hinaus, "python -m app.main" lief
# nie an, Port 8080 antwortete nicht -- der Container galt als "Up" und tat
# nichts. Gemessen im betroffenen Container: IPv6 scheitert (HTTP 000), IPv4
# antwortet in 0,6 s.
export NODE_OPTIONS="${NODE_OPTIONS:+$NODE_OPTIONS }--dns-result-order=ipv4first"

# Frist fuer EINEN Aktualisierungsversuch. Grosszuegig, weil ein Abbruch hier
# dank der Zwischenablage (siehe update_cli) folgenlos ist: Auf einem Raspberry
# Pi gemessen brauchte allein die Installation von @anthropic-ai/claude-code
# 51 Sekunden -- die frueheren 60 s trafen genau die Abbruchkante.
NPM_FRIST="${ENTRYPOINT_NPM_TIMEOUT:-300}"

# Zielverzeichnisse. Im Betrieb immer die echten; die Tests biegen sie um,
# damit sie das Verhalten an einer Attrappe wirklich nachmessen koennen,
# statt nur im Skripttext nach Zeichenketten zu suchen.
MODULE_DIR="${ENTRYPOINT_MODULE_DIR:-/usr/lib/node_modules}"
BIN_DIR="${ENTRYPOINT_BIN_DIR:-/usr/bin}"

# Aktualisiert EIN CLI-Paket, ohne die laufende Installation zu gefaehrden.
#
# Warum der Umweg ueber eine Zwischenablage: "npm install -g" ist NICHT
# atomar. npm benennt das vorhandene Paketverzeichnis zuerst weg und schreibt
# danach das neue. Wird es in diesem Fenster abgebrochen -- Frist abgelaufen,
# Netz weg, Container gestoppt --, bleibt WEDER die alte NOCH die neue Fassung
# zurueck. Am 21.09.2026 genau so passiert: Der Agent meldete im Gespraech
# "No such file or directory: claude", im Container lagen nur noch eine
# halb umbenannte ".claude-code-ZjqcDZyQ" und ein unbrauchbares Verzeichnis --
# und die Meldung des Startskripts behauptete dabei treuherzig, man behalte
# "the installed version".
#
# Deshalb: erst vollstaendig in ein Ausweichverzeichnis installieren, und erst
# wenn das geglueckt ist, per Umbenennen einhaengen. Bis dahin bleibt die
# vorhandene Installation unberuehrt; schlaegt etwas fehl, ist schlicht nichts
# geschehen.
update_cli() {
  local pkg="$1" bin="$2"
  local stage log
  stage="$(mktemp -d)"
  # Physischen Pfad verwenden: "readlink -f" weiter unten loest auch die
  # Verzeichnis-Verknuepfungen darueber auf (auf macOS zeigt /var auf
  # /private/var). Stuenden hier zwei Schreibweisen desselben Ortes, schluge
  # das Abschneiden des Praefixes fehl und die Verknuepfungen zeigten ins Leere.
  stage="$(cd "$stage" && pwd -P)"
  log="/tmp/entrypoint-npm-${bin}.log"

  if ! timeout -k 10 "$NPM_FRIST" npm install -g --prefix "$stage" "${pkg}@latest" >"$log" 2>&1; then
    rm -rf "$stage"
    echo "[entrypoint] ${pkg} nicht aktualisiert (offline, Registry-Fehler oder Frist) -- vorhandene Fassung bleibt unangetastet"
    return 0
  fi

  local src="$stage/lib/node_modules/$pkg"
  if [ ! -d "$src" ]; then
    rm -rf "$stage"
    echo "[entrypoint] ${pkg} nicht aktualisiert (unerwartetes Ergebnis in der Zwischenablage) -- vorhandene Fassung bleibt unangetastet"
    return 0
  fi

  # Die Verknuepfungen zeigen noch in die Zwischenablage. Ziele merken,
  # BEVOR das Verzeichnis umzieht.
  local bins="" lnk name ziel
  for lnk in "$stage"/bin/*; do
    [ -e "$lnk" ] || continue
    name="$(basename "$lnk")"
    ziel="$(readlink -f "$lnk")"
    bins="${bins}${name}	${ziel#"$stage"/lib/node_modules/}
"
  done

  local dst="$MODULE_DIR/$pkg"
  # Wohin die bisherige Verknuepfung zeigt -- wird fuer den Rueckweg gebraucht.
  local alt_ziel=""
  [ -L "$BIN_DIR/$bin" ] && alt_ziel="$(readlink "$BIN_DIR/$bin")"
  mkdir -p "$(dirname "$dst")"
  rm -rf "$dst.vorher"
  [ -e "$dst" ] && mv "$dst" "$dst.vorher"
  # Gleiches Dateisystem -> Umbenennen statt Kopieren, also kein Fenster, in
  # dem ein halbes Paket dasteht.
  if ! mv "$src" "$dst"; then
    [ -e "$dst.vorher" ] && mv "$dst.vorher" "$dst"
    [ -n "$alt_ziel" ] && ln -sf "$alt_ziel" "$BIN_DIR/$bin"
    rm -rf "$stage"
    echo "[entrypoint] ${pkg} nicht aktualisiert (Einhaengen fehlgeschlagen) -- vorhandene Fassung wiederhergestellt"
    return 0
  fi

  printf '%s' "$bins" | while IFS='	' read -r name ziel; do
    [ -n "$name" ] || continue
    ln -sf "$MODULE_DIR/$ziel" "$BIN_DIR/$name"
    chmod +x "$MODULE_DIR/$ziel" 2>/dev/null || true
  done
  rm -rf "$stage"

  # Letzte Probe: laeuft das Ergebnis ueberhaupt? Wenn nicht, zurueck auf die
  # Fassung, die eben noch funktioniert hat -- ein Agent ohne CLI ist das
  # einzige Ergebnis, das hier nicht herauskommen darf.
  #
  # Geprueft wird gezielt die soeben eingehaengte Datei, nicht das, was der
  # Suchpfad gerade findet: Sonst koennte eine gleichnamige CLI woanders im
  # Pfad einen kaputten Einbau als geglueckt durchgehen lassen.
  if [ -x "$BIN_DIR/$bin" ] && "$BIN_DIR/$bin" --version >/dev/null 2>&1; then
    rm -rf "$dst.vorher"
    echo "[entrypoint] ${pkg} aktuell ($("$BIN_DIR/$bin" --version 2>/dev/null | head -1))"
  elif [ -e "$dst.vorher" ]; then
    rm -rf "$dst"
    mv "$dst.vorher" "$dst"
    [ -n "$alt_ziel" ] && ln -sf "$alt_ziel" "$BIN_DIR/$bin"
    echo "[entrypoint] ${pkg} liess sich nach der Aktualisierung nicht starten -- vorherige Fassung wiederhergestellt"
  else
    echo "[entrypoint] WARNUNG: ${bin} ist nach der Aktualisierung nicht lauffaehig und es gab keine vorherige Fassung"
  fi
}

# Nur die Funktionen bereitstellen -- die Tests binden diese Datei ein und
# rufen update_cli danach gezielt auf.
[ "${ENTRYPOINT_NUR_DEFINIEREN:-}" = "1" ] && return 0

update_cli "@anthropic-ai/claude-code" "claude"
update_cli "@openai/codex" "codex"

exec gosu agent "$@"
