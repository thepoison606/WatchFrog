#!/bin/zsh
set -euo pipefail

PLIST_FILE="$HOME/Library/LaunchAgents/de.watchfrog.plist"
LABEL="de.watchfrog"

echo
echo "WatchFrog – macOS-Autostart entfernen"
echo "======================================"
echo

launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
if [[ -f "$PLIST_FILE" ]]; then
  rm "$PLIST_FILE"
fi

echo "Der WatchFrog-Autostart wurde entfernt."
echo "Konfiguration und Logs bleiben erhalten."
echo
read "?Zum Schließen Eingabetaste drücken …"
