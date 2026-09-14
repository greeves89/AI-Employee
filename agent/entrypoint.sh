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

update_cli() {
  local pkg="$1"
  if timeout 60 npm install -g "${pkg}@latest" >/tmp/entrypoint-npm-update.log 2>&1; then
    echo "[entrypoint] ${pkg} up to date ($(npm list -g "${pkg}" --depth=0 2>/dev/null | tail -1))"
  else
    echo "[entrypoint] ${pkg} update skipped (offline, registry error, or timeout) -- keeping installed version"
  fi
}

update_cli "@anthropic-ai/claude-code"
update_cli "@openai/codex"

exec gosu agent "$@"
