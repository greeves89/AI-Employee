#!/usr/bin/env bash
# Auto-commit every Second Brain vault under $SECONDBRAIN_BASE.
#
# LOCAL git only — no remote is ever configured and nothing is pushed. This gives
# per-file diff / history / rollback for the department knowledge vaults while
# keeping all data on-prem (DSGVO). "Who/when" is correlated via the app's
# FILE_WRITTEN audit events; "what/diff" comes from these commits.
#
# Intended to run from a systemd timer (see deploy/secondbrain-autocommit.timer).
set -euo pipefail

BASE="${SECONDBRAIN_BASE:-/srv/secondbrain}"
[ -d "$BASE" ] || exit 0

for d in "$BASE"/*/; do
  [ -d "$d" ] || continue
  # The orchestrator image has no git, so a UI-created vault arrives without a
  # repo — initialise it here (the host has git). LOCAL only, no remote.
  if [ ! -d "${d}.git" ]; then
    git -C "$d" init -q || continue
    git -C "$d" config user.email secondbrain@ai-employee.local
    git -C "$d" config user.name "Second Brain"
  fi
  if [ -n "$(git -C "$d" status --porcelain 2>/dev/null)" ]; then
    git -C "$d" add -A
    git -C "$d" -c user.email=secondbrain@ai-employee.local -c user.name="Second Brain" \
      commit -q -m "auto: snapshot $(date -u +%FT%TZ)" || true
  fi
done
