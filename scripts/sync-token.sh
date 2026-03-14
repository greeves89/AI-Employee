#!/bin/bash
# Syncs the Claude OAuth token from macOS Keychain to a host directory
# that is bind-mounted into the orchestrator container.
#
# The orchestrator reads /host-auth/token.json every 2 minutes.
# Your local Claude Code CLI manages the token lifecycle (refresh via Keychain).
# This script just copies the current token so the server can use it.
#
# Usage:
#   ./scripts/sync-token.sh              # Run once
#   launchctl load ~/Library/LaunchAgents/com.ai-employee.sync-token.plist  # Auto-run

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
AUTH_DIR="$REPO_DIR/host-auth"
TOKEN_FILE="$AUTH_DIR/token.json"
KEYCHAIN_SERVICE="Claude Code-credentials"

# Extract credentials JSON from Keychain
CREDS=$(security find-generic-password -s "$KEYCHAIN_SERVICE" -w 2>/dev/null) || {
    echo "[sync-token] ERROR: Could not read from Keychain" >&2
    exit 1
}

# Parse access token and expiry
ACCESS_TOKEN=$(echo "$CREDS" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('claudeAiOauth',{}).get('accessToken',''))")
EXPIRES_AT=$(echo "$CREDS" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('claudeAiOauth',{}).get('expiresAt',0))")

if [ -z "$ACCESS_TOKEN" ]; then
    echo "[sync-token] ERROR: No accessToken in Keychain" >&2
    exit 1
fi

# Skip if unchanged
if [ -f "$TOKEN_FILE" ]; then
    OLD_TOKEN=$(python3 -c "import json; print(json.load(open('$TOKEN_FILE')).get('access_token',''))" 2>/dev/null || echo "")
    if [ "$OLD_TOKEN" = "$ACCESS_TOKEN" ]; then
        exit 0
    fi
fi

# Write token file
mkdir -p "$AUTH_DIR"
python3 -c "
import json, sys
from datetime import datetime, timezone
json.dump({
    'access_token': sys.argv[1],
    'expires_at': int(sys.argv[2]),
    'updated_at': datetime.now(timezone.utc).isoformat(),
    'source': 'keychain'
}, open(sys.argv[3], 'w'))
" "$ACCESS_TOKEN" "$EXPIRES_AT" "$TOKEN_FILE"

echo "[sync-token] Token synced (…${ACCESS_TOKEN: -8}) at $(date '+%H:%M:%S')"
