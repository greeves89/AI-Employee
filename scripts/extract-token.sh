#!/bin/bash
# Extract Claude OAuth token from macOS Keychain
# The token is stored by Claude Code CLI after login

echo "=== Claude OAuth Token Extraction ==="
echo ""
echo "Method 1: Check macOS Keychain"
echo "  1. Open 'Keychain Access' app"
echo "  2. Search for 'claude' or 'anthropic'"
echo "  3. Double-click the entry"
echo "  4. Check 'Show password'"
echo "  5. Copy the token"
echo ""
echo "Method 2: Check Claude config"
CONFIG_FILE="$HOME/Library/Application Support/Claude/config.json"
if [ -f "$CONFIG_FILE" ]; then
    echo "Found Claude config at: $CONFIG_FILE"
    echo "Note: The token in this file is encrypted."
    echo ""
else
    echo "Claude config not found at expected location."
    echo "Make sure Claude Code CLI is installed and you've logged in."
fi
echo ""
echo "Once you have the token, add it to your .env file:"
echo "  CLAUDE_CODE_OAUTH_TOKEN=<your-token>"
