#!/bin/zsh
set -euo pipefail

PLIST_FILE="$HOME/Library/LaunchAgents/de.watchfrog.plist"
LABEL="de.watchfrog"

echo
echo "WatchFrog – remove macOS autostart"
echo "==================================="
echo

launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
if [[ -f "$PLIST_FILE" ]]; then
  rm "$PLIST_FILE"
fi

echo "WatchFrog autostart has been removed."
echo "The configuration and logs have been preserved."
echo
read "?Press Enter to close …"
