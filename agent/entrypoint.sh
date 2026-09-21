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
# -- keep whatever version is already installed and move on.
set -eu

# Node bevorzugt von sich aus IPv6. Loest die Registry nur zu IPv6-Adressen
# auf und fehlt im Container die IPv6-Route, laeuft npm nicht in einen Fehler,
# sondern in einen HAENGER. Am 21.09.2026 stand ein Agent deshalb still: Das
# Startskript kam nie ueber diesen Aufruf hinaus, "python -m app.main" lief
# nie an, Port 8080 antwortete nicht -- der Container galt als "Up" und tat
# nichts. Gemessen im betroffenen Container: IPv6 scheitert (HTTP 000), IPv4
# antwortet in 0,6 s.
export NODE_OPTIONS="${NODE_OPTIONS:+$NODE_OPTIONS }--dns-result-order=ipv4first"

update_cli() {
  local pkg="$1"
  # "-k 10": Nach der Frist folgt SIGKILL. Ohne das haelt der Deckel nicht --
  # ein einfaches "timeout 60" schickt nur SIGTERM, und npm stirbt daran
  # nicht. Nachgemessen an einem Prozess, der SIGTERM ignoriert: mit
  # schlichtem "timeout 5" dauerte es 61 s, mit "timeout -k 3 5" acht.
  # Genau daran hing der Agent oben drei Minuten, obwohl 60 s dastanden.
  if timeout -k 10 60 npm install -g "${pkg}@latest" >/tmp/entrypoint-npm-update.log 2>&1; then
    echo "[entrypoint] ${pkg} up to date ($(npm list -g "${pkg}" --depth=0 2>/dev/null | tail -1))"
  else
    echo "[entrypoint] ${pkg} update skipped (offline, registry error, or timeout) -- keeping installed version"
  fi
}

update_cli "@anthropic-ai/claude-code"
update_cli "@openai/codex"

exec gosu agent "$@"
