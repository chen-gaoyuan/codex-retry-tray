#!/bin/zsh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$HOME/.codex/auto-retry"
mkdir -p "$DEST" "$HOME/Library/LaunchAgents"
cp "$ROOT/src/codex_auto_retry.py" "$DEST/codex-auto-retry.py"
mkdir -p "$DEST/Codex Auto Retry.app/Contents/MacOS"
cp "$ROOT/src/CodexAutoRetryMenuBar.swift" "$DEST/CodexAutoRetryMenuBar.swift"
cp "$ROOT/config.default.json" "$DEST/config.json"
chmod 600 "$DEST/config.json"
if [[ ! -x "$DEST/Codex Auto Retry.app/Contents/MacOS/CodexAutoRetryMenuBar" ]]; then
  "$ROOT/scripts/build_macos.sh" >/dev/null
  cp -R "$ROOT/dist/macos/Codex Auto Retry.app/Contents" "$DEST/Codex Auto Retry.app/"
else
  cp -R "$ROOT/dist/macos/Codex Auto Retry.app/Contents" "$DEST/Codex Auto Retry.app/"
fi
chmod 700 "$DEST/Codex Auto Retry.app/Contents/MacOS/CodexAutoRetryMenuBar"
MENU_PLIST="$HOME/Library/LaunchAgents/com.openai.codex-auto-retry.menu-bar.plist"
cat > "$MENU_PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.openai.codex-auto-retry.menu-bar</string>
<key>ProgramArguments</key><array><string>$DEST/Codex Auto Retry.app/Contents/MacOS/CodexAutoRetryMenuBar</string></array>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/><key>ThrottleInterval</key><integer>10</integer><key>ProcessType</key><string>Interactive</string>
<key>StandardOutPath</key><string>$DEST/menu-bar.stdout.log</string><key>StandardErrorPath</key><string>$DEST/menu-bar.stderr.log</string>
</dict></plist>
PLIST_EOF
chmod 600 "$MENU_PLIST"
launchctl bootout "gui/$(id -u)"/com.openai.codex-auto-retry 2>/dev/null || true
launchctl bootout "gui/$(id -u)"/com.openai.codex-auto-retry.menu-bar 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$MENU_PLIST"
launchctl kickstart -k "gui/$(id -u)/com.openai.codex-auto-retry.menu-bar"
echo "Codex Auto Retry installed for $USER"
