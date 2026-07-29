#!/usr/bin/env bash
set -u

CONFIG_ROOT="${XDG_CONFIG_HOME:-$HOME/.config}/watchfrog"
LOG_FILE="$CONFIG_ROOT/logs/watchfrog.log"
SERVICE_NAME="watchfrog.service"

echo
echo "WatchFrog – Linux status"
echo "========================"
echo

if systemctl --user is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
  echo "Status: running in the background"
else
  echo "Status: not running"
  echo "Please run setup-linux.sh."
fi

echo
if [[ -f "$LOG_FILE" ]]; then
  echo "Latest messages:"
  echo
  tail -n 25 "$LOG_FILE"
else
  echo "No log file exists yet at:"
  echo "$LOG_FILE"
fi
