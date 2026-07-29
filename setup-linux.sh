#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MONITOR_SCRIPT="$SCRIPT_DIR/watchfrog.py"
XDG_ROOT="${XDG_CONFIG_HOME:-$HOME/.config}"
CONFIG_ROOT="$XDG_ROOT/watchfrog"
CONFIG_FILE="$CONFIG_ROOT/config.toml"
SERVICE_DIR="$XDG_ROOT/systemd/user"
SERVICE_FILE="$SERVICE_DIR/watchfrog.service"

echo
echo "WatchFrog – Linux-Einrichtung"
echo "============================="
echo

PYTHON_BIN="$(command -v python3 || true)"
if [[ -z "$PYTHON_BIN" ]] ||
   ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
  echo "Benötigt wird Python 3.11 oder neuer."
  exit 1
fi

FFMPEG_BIN="$(command -v ffmpeg || true)"
if [[ -z "$FFMPEG_BIN" ]]; then
  echo "ffmpeg wurde nicht gefunden. Bitte über die Paketverwaltung installieren."
  exit 1
fi

mkdir -p "$CONFIG_ROOT"
if [[ ! -f "$CONFIG_FILE" ]]; then
  "$PYTHON_BIN" "$MONITOR_SCRIPT" --configure --config "$CONFIG_FILE"
fi

"$PYTHON_BIN" "$MONITOR_SCRIPT" --check --config "$CONFIG_FILE"
"$PYTHON_BIN" "$MONITOR_SCRIPT" --test-telegram --config "$CONFIG_FILE"
"$PYTHON_BIN" "$MONITOR_SCRIPT" --test-healthcheck --config "$CONFIG_FILE"

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemd wurde nicht gefunden. Manueller Start:"
  echo "$PYTHON_BIN $MONITOR_SCRIPT --config $CONFIG_FILE"
  exit 0
fi

mkdir -p "$SERVICE_DIR"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=WatchFrog Audio Stream Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart="$PYTHON_BIN" "$MONITOR_SCRIPT" --config "$CONFIG_FILE"
WorkingDirectory="$SCRIPT_DIR"
Environment="PATH=$(dirname "$FFMPEG_BIN"):/usr/local/bin:/usr/bin:/bin"
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now watchfrog.service

echo
echo "Fertig: WatchFrog läuft als systemd-Benutzerdienst."
echo "Logdatei: $CONFIG_ROOT/logs/watchfrog.log"
