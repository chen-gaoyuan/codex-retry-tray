#!/bin/zsh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/dist/macos"
APP="$OUT/Codex Auto Retry.app"
BUILD="$ROOT/.build/macos"
VENV="$BUILD/venv"
mkdir -p "$OUT/$(basename "$APP")/Contents/MacOS" "$OUT/$(basename "$APP")/Contents/Resources" "$BUILD/pyinstaller-dist" "$BUILD/pyinstaller-work" "$BUILD/pyinstaller-spec"
if [[ ! -x "$VENV/bin/python" ]]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip pyinstaller
"$VENV/bin/python" -m PyInstaller --noconfirm --clean --onefile \
  --name CodexAutoRetryWatcher \
  --distpath "$BUILD/pyinstaller-dist" \
  --workpath "$BUILD/pyinstaller-work" \
  --specpath "$BUILD/pyinstaller-spec" \
  "$ROOT/src/codex_auto_retry.py" >/dev/null
swiftc -O -target arm64-apple-macosx27.0 -framework Cocoa \
  "$ROOT/src/CodexAutoRetryMenuBar.swift" \
  -o "$APP/Contents/MacOS/CodexAutoRetryMenuBar"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDisplayName</key><string>Codex Auto Retry</string>
  <key>CFBundleExecutable</key><string>CodexAutoRetryMenuBar</string>
  <key>CFBundleIdentifier</key><string>com.openai.codex-auto-retry.menu-bar</string>
  <key>CFBundleName</key><string>Codex Auto Retry</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSUIElement</key><true/>
  <key>LSMinimumSystemVersion</key><string>27.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST
chmod 700 "$APP/Contents/MacOS/CodexAutoRetryMenuBar"
cp "$ROOT/src/codex_auto_retry.py" "$OUT/codex-auto-retry.py"
cp "$ROOT/src/codex_auto_retry.py" "$APP/Contents/Resources/codex-auto-retry.py"
cp "$BUILD/pyinstaller-dist/CodexAutoRetryWatcher" "$APP/Contents/Resources/CodexAutoRetryWatcher"
chmod 700 "$APP/Contents/Resources/CodexAutoRetryWatcher"
cp "$ROOT/config.default.json" "$OUT/config.default.json"
cp "$ROOT/scripts/install_macos.sh" "$OUT/install_macos.sh"
chmod 755 "$OUT/install_macos.sh"
plutil -lint "$APP/Contents/Info.plist"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$OUT/CodexAutoRetry-macos-arm64.app.zip"
echo "Built $OUT"
