#!/bin/bash
# Unblock_AdoptIQ.command
#
# Clears macOS Gatekeeper's "com.apple.quarantine" attribute from the installed
# AdoptIQ.app and launches it. Required after dragging AdoptIQ to the
# Applications folder from a downloaded DMG, because the app is signed adhoc
# (no Apple Developer ID) and Apple Silicon Gatekeeper / AMFI will silently
# kill quarantined adhoc-signed apps on launch (the Dock icon bounces and dies).
#
# Safe to run multiple times. You only need to run it once per install/update.

set -e

APP="/Applications/AdoptIQ.app"

if [ ! -d "$APP" ]; then
  /usr/bin/osascript -e 'display dialog "AdoptIQ.app is not in /Applications yet.\n\nDrag AdoptIQ from the DMG window to the Applications folder, then run \"Unblock AdoptIQ.command\" again." buttons {"OK"} default button 1 with title "AdoptIQ" with icon caution'
  exit 1
fi

/usr/bin/xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true
/usr/bin/xattr -cr "$APP" 2>/dev/null || true

/usr/bin/open "$APP"

echo "AdoptIQ unblocked and launched. You can close this window."
