#!/usr/bin/env bash
# Build a proper, double-clickable "localdeck.app" using macOS's own
# osacompile. Unlike a hand-rolled shell-script bundle, this produces a real
# app launcher that macOS will open reliably (no endless Dock bouncing).
#
# Run this ONCE on your Mac:
#     bash build_macos_app.sh
#
# Then double-click "localdeck.app". (First launch: right-click -> Open.)
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP="$ROOT_DIR/localdeck.app"
PORT="${LOCALDECK_PORT:-8900}"

if ! command -v osacompile >/dev/null 2>&1; then
  echo "osacompile not found (it ships with macOS). Are you on a Mac?" >&2
  exit 1
fi

echo "Removing any previous app bundle..."
rm -rf "$APP"

SRC="$(mktemp -t localdeck_XXXX).applescript"
cat > "$SRC" <<OSA
property projectRoot : "$ROOT_DIR"
property thePort : "$PORT"

on run
	set theURL to "http://127.0.0.1:" & thePort
	-- If it's already running, just open the dashboard in the browser.
	if isHealthy(theURL) then
		do shell script "open " & quoted form of theURL
		return
	end if
	-- Otherwise start it in a visible Terminal window using your real login
	-- shell (full PATH). launch.sh builds the UI if needed, starts the server,
	-- and opens the browser once it's healthy. Ctrl+C in that window stops it.
	tell application "Terminal"
		activate
		do script "cd " & quoted form of projectRoot & " && ./launch.sh"
	end tell
end run

on isHealthy(theURL)
	try
		do shell script "curl -s -o /dev/null --max-time 2 " & quoted form of (theURL & "/api/health")
		return true
	on error
		return false
	end try
end isHealthy
OSA

echo "Compiling app bundle..."
osacompile -o "$APP" "$SRC"
rm -f "$SRC"

# Use our custom icon.
if [[ -f "$ROOT_DIR/assets/appicon.icns" ]]; then
  cp "$ROOT_DIR/assets/appicon.icns" "$APP/Contents/Resources/applet.icns"
fi

# Clear quarantine so it opens without the "damaged" warning.
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true
# Refresh icon cache for this bundle.
touch "$APP"

echo ""
echo "Built: $APP"
echo "Double-click it in Finder. First time: right-click -> Open."
