#!/bin/bash
# Extract Claude OAuth token from system credential store
# Works on macOS, Linux, and Windows (Git Bash / WSL)
# The token is stored by Claude Code CLI after "claude login"

echo "=== Claude OAuth Token Extraction ==="
echo ""

detect_os() {
  case "$(uname -s)" in
    Darwin*)  echo "macos" ;;
    Linux*)
      if grep -qi microsoft /proc/version 2>/dev/null; then
        echo "wsl"
      else
        echo "linux"
      fi
      ;;
    MINGW*|MSYS*|CYGWIN*) echo "windows" ;;
    *)        echo "unknown" ;;
  esac
}

OS=$(detect_os)
echo "Detected OS: $OS"
echo ""

case "$OS" in
  macos)
    echo "=== macOS: Keychain Access ==="
    echo ""
    echo "Method 1: Automatic extraction"
    echo "  Trying to read from Keychain..."
    TOKEN=$(security find-generic-password -s "claude.ai" -w 2>/dev/null || \
            security find-generic-password -s "claude-code" -w 2>/dev/null || \
            security find-generic-password -s "anthropic" -w 2>/dev/null)
    if [ -n "$TOKEN" ]; then
      echo "  Found token! (${#TOKEN} chars)"
      echo "  First 20 chars: ${TOKEN:0:20}..."
      echo ""
      echo "  Add to .env:"
      echo "  CLAUDE_CODE_OAUTH_TOKEN=$TOKEN"
    else
      echo "  Could not auto-extract. Try manually:"
      echo "  1. Open 'Keychain Access' app"
      echo "  2. Search for 'claude' or 'anthropic'"
      echo "  3. Double-click the entry"
      echo "  4. Check 'Show password'"
      echo "  5. Copy the token"
    fi
    echo ""
    echo "Method 2: Check Claude config"
    CONFIG_FILE="$HOME/Library/Application Support/Claude/config.json"
    if [ -f "$CONFIG_FILE" ]; then
      echo "  Found Claude config at: $CONFIG_FILE"
    fi
    ;;

  linux)
    echo "=== Linux: Secret Service (libsecret) ==="
    echo ""
    echo "Method 1: secret-tool (GNOME Keyring / KDE Wallet)"
    if command -v secret-tool &>/dev/null; then
      TOKEN=$(secret-tool lookup service claude.ai 2>/dev/null || \
              secret-tool lookup service claude-code 2>/dev/null)
      if [ -n "$TOKEN" ]; then
        echo "  Found token! (${#TOKEN} chars)"
        echo "  First 20 chars: ${TOKEN:0:20}..."
        echo ""
        echo "  Add to .env:"
        echo "  CLAUDE_CODE_OAUTH_TOKEN=$TOKEN"
      else
        echo "  No token found via secret-tool."
        echo "  Try: secret-tool search --all service claude"
      fi
    else
      echo "  secret-tool not installed."
      echo "  Install: sudo apt install libsecret-tools (Debian/Ubuntu)"
      echo "           sudo dnf install libsecret (Fedora)"
    fi
    echo ""
    echo "Method 2: Check Claude config files"
    for dir in "$HOME/.claude" "$HOME/.config/claude"; do
      if [ -d "$dir" ]; then
        echo "  Found config dir: $dir"
        ls -la "$dir" 2>/dev/null | grep -E "cred|token|auth|config" || true
      fi
    done
    ;;

  wsl)
    echo "=== WSL: Windows Credential Manager (via PowerShell) ==="
    echo ""
    echo "Method 1: Read from Windows Credential Manager"
    echo "  Run in PowerShell (not WSL):"
    echo ""
    echo '  [System.Runtime.InteropServices.Marshal]::PtrToStringAuto('
    echo '    [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR('
    echo '      (Get-StoredCredential -Target "claude.ai").Password'
    echo '    )'
    echo '  )'
    echo ""
    echo "  Or use cmdkey:"
    echo '  cmdkey /list | findstr "claude"'
    echo ""
    echo "Method 2: Check Claude config in Windows"
    WIN_HOME=$(wslpath "$(cmd.exe /C 'echo %USERPROFILE%' 2>/dev/null | tr -d '\r')" 2>/dev/null)
    if [ -n "$WIN_HOME" ]; then
      for dir in "$WIN_HOME/AppData/Roaming/Claude" "$WIN_HOME/.claude"; do
        if [ -d "$dir" ]; then
          echo "  Found config dir: $dir"
          ls -la "$dir" 2>/dev/null | grep -E "cred|token|auth|config" || true
        fi
      done
    fi
    ;;

  windows)
    echo "=== Windows: Credential Manager (Git Bash) ==="
    echo ""
    echo "Method 1: PowerShell (recommended)"
    echo "  Open PowerShell and run:"
    echo ""
    echo '  # If CredentialManager module installed:'
    echo '  (Get-StoredCredential -Target "claude.ai").Password |'
    echo '    ForEach-Object { [System.Runtime.InteropServices.Marshal]::PtrToStringAuto('
    echo '      [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($_)) }'
    echo ""
    echo "Method 2: cmdkey"
    echo '  cmdkey /list | findstr "claude"'
    echo ""
    echo "Method 3: Check Claude config"
    CONFIG_DIR="$APPDATA/Claude"
    if [ -d "$CONFIG_DIR" ]; then
      echo "  Found config dir: $CONFIG_DIR"
      ls -la "$CONFIG_DIR" 2>/dev/null | grep -E "cred|token|auth|config" || true
    else
      echo "  Config dir not found at: $CONFIG_DIR"
      echo "  Also check: $USERPROFILE/.claude/"
    fi
    ;;

  *)
    echo "Unknown OS. Please manually find the Claude OAuth token."
    echo ""
    echo "Common locations:"
    echo "  macOS:   Keychain Access → search 'claude'"
    echo "  Linux:   secret-tool lookup service claude.ai"
    echo "  Windows: Credential Manager → search 'claude'"
    echo "  Config:  ~/.claude/ or ~/.config/claude/"
    ;;
esac

echo ""
echo "─────────────────────────────────────"
echo "Once you have the token, add it to your .env file:"
echo "  CLAUDE_CODE_OAUTH_TOKEN=<your-token>"
echo ""
echo "Or paste it in the Settings → Anthropic Direct → OAuth Token field."
