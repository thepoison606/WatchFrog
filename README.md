# WatchFrog

<p><img src="assets/watchfrog-app-icon-rounded.png" alt="WatchFrog icon" width="150" height="150" align="left" hspace="12">A silence detection watchdog for audio streams.</p>

A Telegram alert is triggered only when a stream is still being received but its
decoded audio level remains below the configured threshold for longer than the
configured duration. Network interruptions, unavailable playlists, and decoder
restarts are logged and automatically reconnected, but do not trigger an
alert.<br clear="left">

## Requirements

- Python 3.11 or newer
- `ffmpeg` available in the system path
- a Telegram bot
- optionally, a Healthchecks.io ping URL

Create the Telegram bot through `@BotFather` using `/newbot`.

## Setup by operating system

### macOS

Double-click `setup-macos.command`. The setup assistant configures Telegram and
optionally Healthchecks.io, asks for the streams to monitor, and installs the
WatchFrog LaunchAgent.

Use `status-macos.command` to view the status and log.
`uninstall-macos.command` removes the automatic startup entry.

Install missing dependencies with:

```sh
brew install python ffmpeg
```

### Linux

```sh
chmod +x setup-linux.sh
./setup-linux.sh
```

The script installs a systemd user service:

```sh
./status-linux.sh
./uninstall-linux.sh
```

To keep the service running without an active login session, enable systemd
lingering for the user.

### Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1
powershell -ExecutionPolicy Bypass -File .\status-windows.ps1
```

WatchFrog then runs as a scheduled task. `uninstall-windows.ps1` removes that
task. You can install `ffmpeg`, for example, with
`winget install Gyan.FFmpeg`.

### Portable or manual operation

```sh
python watchfrog.py --configure
python watchfrog.py --check
python watchfrog.py
```

Alternatively, install and run it as a regular Python command:

```sh
python -m pip install .
watchfrog --configure
watchfrog
```

Without `--config`, WatchFrog uses:

- Windows: `%APPDATA%\WatchFrog\config.toml`
- Linux/macOS: `$XDG_CONFIG_HOME/watchfrog/config.toml` or
  `~/.config/watchfrog/config.toml`
- portable mode: an existing `config.toml` next to `watchfrog.py`

The rotating `watchfrog.log` file is stored in the `logs` subdirectory next to
the active configuration.

Add every stream to the `[streams]` table using a freely chosen name and its
HTTP or HTTPS URL:

```toml
[streams]
"Main Stream" = "https://stream.example.com/main.m3u"
"Backup Stream" = "https://stream.example.com/backup.m3u8"
```

## Configuration

Global defaults:

```toml
[monitor]
silence_seconds = 5.0
silence_threshold_db = -60.0
recovery_seconds = 0.5
reconnect_delay_seconds = 1.0
```

- `silence_seconds`: duration of received silence before an alert
- `silence_threshold_db`: RMS level threshold in dBFS
- `recovery_seconds`: duration of audible audio before a recovery notification
- `reconnect_delay_seconds`: delay before a new connection attempt

Timing values can be overridden per stream. Omitted values inherit the global
default:

```toml
[stream_overrides."Main Stream"]
silence_seconds = 8.0
recovery_seconds = 1.0
reconnect_delay_seconds = 2.0

[stream_overrides."Backup Stream"]
silence_seconds = 12.0
```

The name must exactly match an entry in `[streams]`. An active silence measurement
is discarded when reception is interrupted; separate silence periods are not
combined.

## Healthchecks.io

When `[healthchecks] ping_url` is set, WatchFrog sends a heartbeat immediately at
startup and every 60 seconds thereafter. The heartbeat is sent only while all
stream monitoring tasks are running. A one-minute period and approximately two
minutes of grace time are recommended.

```sh
python watchfrog.py --test-telegram
python watchfrog.py --test-healthcheck
```

## Releases

Pushing a tag that starts with `v`, for example `v2.0.0`, runs the GitHub Actions
release workflow. It executes the test suite, builds a versioned portable ZIP
archive, and publishes a GitHub release with automatically generated notes.
