#!/bin/zsh
set -u

SCRIPT_DIR="${0:A:h}"
LABEL="de.watchfrog"
LOG_FILE="$SCRIPT_DIR/logs/watchfrog.log"

echo
echo "WatchFrog – macOS-Status"
echo "========================"
echo

if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  echo "Status: läuft im Hintergrund"
else
  echo "Status: nicht gestartet"
  echo "Bitte setup-macos.command ausführen."
fi

echo
if [[ -f "$LOG_FILE" ]]; then
  echo "Letzte Meldungen:"
  echo
  tail -n 25 "$LOG_FILE"
else
  echo "Es gibt noch keine Logdatei."
fi

echo
read "?Zum Schließen Eingabetaste drücken …"
