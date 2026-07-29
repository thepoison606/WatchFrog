#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEST_ROOT"' EXIT

BIN_DIR="$TEST_ROOT/bin"
XDG_CONFIG_HOME="$TEST_ROOT/config"
CONFIG_ROOT="$XDG_CONFIG_HOME/watchfrog"
CONFIG_FILE="$CONFIG_ROOT/config.toml"
SERVICE_FILE="$XDG_CONFIG_HOME/systemd/user/watchfrog.service"
SYSTEMCTL_LOG="$TEST_ROOT/systemctl.log"

mkdir -p "$BIN_DIR" "$CONFIG_ROOT"
touch "$CONFIG_FILE" "$SYSTEMCTL_LOG"

cat > "$BIN_DIR/python3" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF

cat > "$BIN_DIR/ffmpeg" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF

cat > "$BIN_DIR/systemctl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$SYSTEMCTL_LOG"
exit 0
EOF

chmod +x "$BIN_DIR/python3" "$BIN_DIR/ffmpeg" "$BIN_DIR/systemctl"

export HOME="$TEST_ROOT/home"
export XDG_CONFIG_HOME
export SYSTEMCTL_LOG
export PATH="$BIN_DIR:/usr/bin:/bin"

for script in setup-linux.sh status-linux.sh uninstall-linux.sh; do
  bash -n "$PROJECT_ROOT/$script"
done

bash "$PROJECT_ROOT/setup-linux.sh"

test -f "$SERVICE_FILE"
grep -F "ExecStart=\"$BIN_DIR/python3\" \"$PROJECT_ROOT/watchfrog.py\" --config \"$CONFIG_FILE\"" "$SERVICE_FILE"
grep -F "WorkingDirectory=\"$PROJECT_ROOT\"" "$SERVICE_FILE"
grep -Fx -- "--user daemon-reload" "$SYSTEMCTL_LOG"
grep -Fx -- "--user enable --now watchfrog.service" "$SYSTEMCTL_LOG"

STATUS_OUTPUT="$(bash "$PROJECT_ROOT/status-linux.sh")"
grep -F "Status: läuft im Hintergrund" <<<"$STATUS_OUTPUT"

bash "$PROJECT_ROOT/uninstall-linux.sh"

test ! -e "$SERVICE_FILE"
grep -Fx -- "--user disable --now watchfrog.service" "$SYSTEMCTL_LOG"

echo "Linux setup, status, and uninstall scripts passed."
