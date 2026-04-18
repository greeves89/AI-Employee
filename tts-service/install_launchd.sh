#!/bin/bash
# Install TTS service as macOS LaunchAgent (auto-starts on login)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/dev.ai-employee.tts.plist"
LOG_DIR="$HOME/Library/Logs/ai-employee"

mkdir -p "$LOG_DIR"

cat > "$PLIST" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>dev.ai-employee.tts</string>
    <key>ProgramArguments</key>
    <array>
        <string>$SCRIPT_DIR/start_mac.sh</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/tts-service.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/tts-service.log</string>
    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>
</dict>
</plist>
EOF

chmod +x "$SCRIPT_DIR/start_mac.sh"
launchctl load "$PLIST"

echo "✅ TTS service registered as LaunchAgent."
echo "   Starts automatically on login."
echo "   Logs: $LOG_DIR/tts-service.log"
echo ""
echo "Stop:    launchctl unload $PLIST"
echo "Restart: launchctl kickstart -k gui/$(id -u)/dev.ai-employee.tts"
