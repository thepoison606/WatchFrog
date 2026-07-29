#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
export PATH="/opt/homebrew/bin:/usr/local/bin:/Library/Frameworks/Python.framework/Versions/Current/bin:$PATH"

APP_NAME="WatchFrog"
MONITOR_SCRIPT="$SCRIPT_DIR/watchfrog.py"
CONFIG_FILE="$SCRIPT_DIR/config.toml"
PLIST_FILE="$HOME/Library/LaunchAgents/de.watchfrog.plist"
LABEL="de.watchfrog"
DOMAIN_TARGET="gui/$(id -u)"
SERVICE_TARGET="$DOMAIN_TARGET/$LABEL"

cd "$SCRIPT_DIR"

echo
echo "$APP_NAME – macOS-Einrichtung"
echo "==============================="
echo

PYTHON_BIN="$(command -v python3 || true)"
if [[ -z "$PYTHON_BIN" ]] ||
   ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
  echo "Benötigt wird Python 3.11 oder neuer."
  echo "Installation mit Homebrew: brew install python"
  echo
  read "?Zum Beenden Eingabetaste drücken …"
  exit 1
fi

FFMPEG_BIN="$(command -v ffmpeg || true)"
if [[ -z "$FFMPEG_BIN" ]]; then
  BREW_BIN="$(command -v brew || true)"
  if [[ -z "$BREW_BIN" ]]; then
    echo "ffmpeg wurde nicht gefunden."
    echo "Installation nach Homebrew: brew install ffmpeg"
    read "?Zum Beenden Eingabetaste drücken …"
    exit 1
  fi
  echo "ffmpeg wird jetzt mit Homebrew installiert."
  "$BREW_BIN" install ffmpeg
  FFMPEG_BIN="$(command -v ffmpeg)"
fi

if [[ -f "$CONFIG_FILE" ]]; then
  echo "Eine bestehende WatchFrog-Konfiguration wurde gefunden."
  read "ANSWER?Telegram-Zugang neu einrichten? [j/N] "
  if [[ "${ANSWER:l}" == "j" || "${ANSWER:l}" == "ja" ]]; then
    "$PYTHON_BIN" "$MONITOR_SCRIPT" --configure --config "$CONFIG_FILE"
  else
    "$PYTHON_BIN" "$MONITOR_SCRIPT" --configure-healthcheck --config "$CONFIG_FILE"
  fi
else
  "$PYTHON_BIN" "$MONITOR_SCRIPT" --configure --config "$CONFIG_FILE"
fi

"$PYTHON_BIN" "$MONITOR_SCRIPT" --check --config "$CONFIG_FILE"
"$PYTHON_BIN" "$MONITOR_SCRIPT" --test-telegram --config "$CONFIG_FILE"
"$PYTHON_BIN" "$MONITOR_SCRIPT" --test-healthcheck --config "$CONFIG_FILE"

mkdir -p "$HOME/Library/LaunchAgents"

"$PYTHON_BIN" - "$PLIST_FILE" "$LABEL" "$PYTHON_BIN" "$MONITOR_SCRIPT" "$CONFIG_FILE" "$SCRIPT_DIR" "$FFMPEG_BIN" <<'PY'
import plistlib
import sys
from pathlib import Path

plist_path, label, python_bin, script, config, working_dir, ffmpeg_bin = sys.argv[1:]
path_entries = [
    str(Path(ffmpeg_bin).parent),
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
]
payload = {
    "Label": label,
    "ProgramArguments": [python_bin, script, "--config", config],
    "WorkingDirectory": working_dir,
    "RunAtLoad": True,
    "KeepAlive": True,
    "ProcessType": "Background",
    "ThrottleInterval": 10,
    "EnvironmentVariables": {"PATH": ":".join(dict.fromkeys(path_entries))},
}
with Path(plist_path).open("wb") as handle:
    plistlib.dump(payload, handle)
PY

launchctl enable "$SERVICE_TARGET" >/dev/null 2>&1 || true
if launchctl print "$SERVICE_TARGET" >/dev/null 2>&1; then
  echo "Vorhandener WatchFrog-Dienst wird neu gestartet."
else
  echo "WatchFrog wird erstmals bei macOS registriert."
  if ! BOOTSTRAP_OUTPUT="$(launchctl bootstrap "$DOMAIN_TARGET" "$PLIST_FILE" 2>&1)"; then
    if ! launchctl print "$SERVICE_TARGET" >/dev/null 2>&1; then
      echo
      echo "macOS konnte WatchFrog nicht registrieren:"
      echo "$BOOTSTRAP_OUTPUT"
      plutil -lint "$PLIST_FILE" || true
      echo "Bitte den Mac neu starten und setup-macos.command erneut ausführen."
      read "?Zum Beenden Eingabetaste drücken …"
      exit 1
    fi
  fi
fi

launchctl kickstart -k "$SERVICE_TARGET"

echo
echo "Fertig: WatchFrog läuft jetzt automatisch im Hintergrund."
echo "Logdatei: $SCRIPT_DIR/logs/watchfrog.log"
echo
read "?Zum Schließen Eingabetaste drücken …"
