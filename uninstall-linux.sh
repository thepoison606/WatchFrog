#!/usr/bin/env bash
set -euo pipefail

SERVICE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_FILE="$SERVICE_DIR/watchfrog.service"

systemctl --user disable --now watchfrog.service 2>/dev/null || true
if [[ -f "$SERVICE_FILE" ]]; then
  rm "$SERVICE_FILE"
fi
systemctl --user daemon-reload

echo "WatchFrog autostart has been removed. The configuration and logs have been preserved."
