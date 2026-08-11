# WatchFrog

<p><img src="assets/watchfrog-app-icon-rounded.png" alt="WatchFrog icon" width="150" height="150" align="left" hspace="12">A silence detection watchdog for audio streams.</p>

Telegram alerts distinguish between received silence and lost reception.
Received silence triggers an alert after its configured duration. Network
interruptions, unavailable playlists, and decoder restarts reconnect
automatically; they trigger a separate reception-outage alert only when no
decodable audio has been received for the configured duration, which defaults
to ten minutes.<br clear="left">

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
reception_outage_seconds = 600.0
silence_threshold_db = -60.0
recovery_seconds = 0.5
reconnect_delay_seconds = 1.0
audio_clip_enabled = true
audio_clip_pre_seconds = 10.0
audio_clip_post_seconds = 5.0
audio_clip_max_outage_seconds = 180.0
```

- `silence_seconds`: duration of received silence before an alert
- `reception_outage_seconds`: duration without received audio before a separate
  reception-outage alert; defaults to 600 seconds (10 minutes)
- `silence_threshold_db`: RMS level threshold in dBFS
- `recovery_seconds`: duration of audible audio before a recovery notification
- `reconnect_delay_seconds`: delay before a new connection attempt
- `audio_clip_enabled`: attach an MP3 after a received-silence recovery
- `audio_clip_pre_seconds`: audio retained before the silence starts
- `audio_clip_post_seconds`: recovered audio included after sound returns
- `audio_clip_max_outage_seconds`: maximum silence duration eligible for an
  audio attachment; the default is 180 seconds (3 minutes)

The regular recovery text notification is always sent when `notify_recovery`
is enabled. For eligible received-silence outages, a separate MP3 follows. It
contains the configured pre-roll, the complete silence, and the configured
post-roll. Longer outages still produce detection and recovery text messages,
but no audio attachment. Reception outages cannot include a clip because no
decodable audio is available while the connection is interrupted.

Timing values can be overridden per stream. Omitted values inherit the global
default:

```toml
[stream_overrides."Main Stream"]
silence_seconds = 8.0
reception_outage_seconds = 900.0
recovery_seconds = 1.0
reconnect_delay_seconds = 2.0

[stream_overrides."Backup Stream"]
silence_seconds = 12.0
```

The name must exactly match an entry in `[streams]`. An active silence
measurement is discarded when reception is interrupted; separate silence
periods are not combined. A reception-outage notification is sent only once per
outage. When decodable audio returns, WatchFrog sends a separate recovery
notification if `notify_recovery` is enabled.

### Scheduled silence thresholds

Each stream can define weekly time slots with a different silence duration.
Outside these slots, the stream's regular `silence_seconds` value remains in
effect:

```toml
[stream_overrides."Main Stream"]
silence_seconds = 5.0

[[stream_overrides."Main Stream".silence_slots]]
days = ["mon", "tue", "wed", "thu", "fri"]
start = "06:00"
end = "10:00"
silence_seconds = 15.0

[[stream_overrides."Main Stream".silence_slots]]
days = ["sat", "sun"]
start = "08:00"
end = "12:00"
silence_seconds = 30.0
```

- `days` accepts English weekday names such as `mon` or `monday`. If omitted,
  the slot applies every day.
- `start` is inclusive and `end` is exclusive, using the timezone configured in
  `[monitor]`.
- A slot can cross midnight, for example `22:00` to `06:00`. Its `days` refer to
  the day on which the slot starts.
- Identical `start` and `end` values cover the complete listed day.
- Overlapping slots for the same stream are rejected during configuration
  validation.
- If the active slot changes during ongoing silence, its new threshold applies
  immediately to the silence already accumulated.

Telegram messages remain queued during network failures, API errors, or rate
limits. WatchFrog retries with a capped backoff, paces consecutive messages,
and preserves message order, so an outage detection is delivered before its
recovery notification.

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

Pushing a version tag, for example `0.1.0`, runs the GitHub Actions release
workflow. It executes the test suite, builds a versioned portable ZIP archive,
and publishes a GitHub release with automatically generated notes.
