#!/bin/bash
# AI-Employee Computer-Use Bridge — Install Script
# Installs the bridge as a macOS LaunchAgent so it auto-starts on login.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRIDGE_SCRIPT="$SCRIPT_DIR/bridge.py"
PLIST_NAME="com.ai-employee.computer-use-bridge"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"
LOG_DIR="$HOME/Library/Logs/ai-employee"
VENV_DIR="$SCRIPT_DIR/.venv"

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC} $*"; }
err()  { echo -e "${RED}✗${NC} $*"; exit 1; }

echo ""
echo "AI-Employee Computer-Use Bridge Installer"
echo "─────────────────────────────────────────"
echo ""

# ── Python venv ───────────────────────────────────────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
  echo "Creating Python virtual environment..."
  python3 -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"

echo "Installing dependencies..."
pip install -q -r "$SCRIPT_DIR/requirements.txt"
ok "Dependencies installed"

# ── Prompt for config ─────────────────────────────────────────────────────────
echo ""
if [ -z "$AI_EMPLOYEE_URL" ]; then
  read -rp "AI-Employee URL (e.g. https://myserver.com): " AI_EMPLOYEE_URL
fi
if [ -z "$AI_EMPLOYEE_TOKEN" ]; then
  read -rsp "JWT Token (from AI-Employee web UI → Settings → API Tokens): " AI_EMPLOYEE_TOKEN
  echo ""
fi

[ -z "$AI_EMPLOYEE_URL" ] && err "URL is required"
[ -z "$AI_EMPLOYEE_TOKEN" ] && err "Token is required"

# Normalize URL (ws/wss for bridge)
WS_URL="${AI_EMPLOYEE_URL/http:\/\//ws://}"
WS_URL="${WS_URL/https:\/\//wss://}"

# ── LaunchAgent plist ─────────────────────────────────────────────────────────
mkdir -p "$LOG_DIR"
mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$PLIST_NAME</string>

  <key>ProgramArguments</key>
  <array>
    <string>$VENV_DIR/bin/python3</string>
    <string>$BRIDGE_SCRIPT</string>
    <string>--url</string>
    <string>$WS_URL</string>
    <string>--token</string>
    <string>$AI_EMPLOYEE_TOKEN</string>
  </array>

  <key>EnvironmentVariables</key>
  <dict>
    <key>AI_EMPLOYEE_URL</key>
    <string>$WS_URL</string>
    <key>AI_EMPLOYEE_TOKEN</key>
    <string>$AI_EMPLOYEE_TOKEN</string>
  </dict>

  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>

  <key>StandardOutPath</key>
  <string>$LOG_DIR/computer-use-bridge.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/computer-use-bridge.log</string>

  <key>ThrottleInterval</key>
  <integer>10</integer>
</dict>
</plist>
EOF

ok "LaunchAgent plist created"

# ── Load the agent ────────────────────────────────────────────────────────────
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"
ok "Bridge started (runs automatically on login)"

echo ""
echo "─────────────────────────────────────────"
echo "Bridge installed and running!"
echo ""
echo "Logs:    $LOG_DIR/computer-use-bridge.log"
echo "Uninstall: launchctl unload $PLIST_PATH && rm $PLIST_PATH"
echo ""
warn "If you see AX tree errors, grant Accessibility access:"
echo "  System Settings → Privacy & Security → Accessibility"
echo "  Add Terminal (or the Python binary at $VENV_DIR/bin/python3)"
echo ""
