#!/bin/bash
# Builds "Screen Translator.app" next to this script. The app runs app.py with
# the Python you build it from (activate your venv first), so keep this folder
# where it is. Rebuild after moving the folder.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="${VIRTUAL_ENV:+$VIRTUAL_ENV/bin/python}"
PY="${PY:-$(command -v python3)}"
"$PY" -c "import AppKit, Vision, ScreenCaptureKit" 2>/dev/null || {
  echo "This Python ($PY) is missing the PyObjC packages - see README step 1."; exit 1; }

APP="$DIR/Screen Translator.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/MacOS/ScreenTranslator" <<EOF
#!/bin/bash
mkdir -p "\$HOME/Library/Logs"
cd "$DIR"
exec "$PY" "$DIR/app.py" >> "\$HOME/Library/Logs/Screen Translator.log" 2>&1
EOF
chmod +x "$APP/Contents/MacOS/ScreenTranslator"

cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Screen Translator</string>
  <key>CFBundleDisplayName</key><string>Screen Translator</string>
  <key>CFBundleIdentifier</key><string>local.screentranslator</string>
  <key>CFBundleExecutable</key><string>ScreenTranslator</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.4</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSScreenCaptureUsageDescription</key>
  <string>Screen Translator reads the text on your screen to translate it. Everything stays on this Mac.</string>
</dict></plist>
EOF

if [ -f "$DIR/icon.png" ]; then
  SET="$(mktemp -d)/AppIcon.iconset"; mkdir -p "$SET"
  for s in 16 32 128 256 512; do
    sips -z $s $s "$DIR/icon.png" --out "$SET/icon_${s}x${s}.png" >/dev/null
    sips -z $((s*2)) $((s*2)) "$DIR/icon.png" --out "$SET/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$SET" -o "$APP/Contents/Resources/AppIcon.icns"
fi
echo "Built: $APP"
echo "First launch: macOS will ask for Screen Recording permission for Screen Translator."
echo "Logs: ~/Library/Logs/Screen Translator.log"
