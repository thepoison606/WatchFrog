#!/usr/bin/env bash
set -u

CONFIG_ROOT="${XDG_CONFIG_HOME:-$HOME/.config}/watchfrog"
LOG_FILE="$CONFIG_ROOT/logs/watchfrog.log"
SERVICE_NAME="watchfrog.service"

echo
echo "WatchFrog – Linux-Status"
echo "========================"
echo

if systemctl --user is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
  echo "Status: läuft im Hintergrund"
else
  echo "Status: nicht gestartet"
  echo "Bitte setup-linux.sh ausführen."
fi

echo
if [[ -f "$LOG_FILE" ]]; then
  echo "Letzte Meldungen:"
  echo
  tail -n 25 "$LOG_FILE"
else
  echo "Es gibt noch keine Logdatei unter:"
  echo "$LOG_FILE"
fi
