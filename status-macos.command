#!/bin/zsh
set -u

SCRIPT_DIR="${0:A:h}"
LABEL="de.watchfrog"
LOG_FILE="$SCRIPT_DIR/logs/watchfrog.log"

echo
echo "WatchFrog – macOS status"
echo "========================"
echo

if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  echo "Status: running in the background"
else
  echo "Status: not running"
  echo "Please run setup-macos.command."
fi

echo
if [[ -f "$LOG_FILE" ]]; then
  echo "Latest messages:"
  echo
  tail -n 25 "$LOG_FILE"
else
  echo "No log file exists yet."
fi

echo
read "?Press Enter to close …"
