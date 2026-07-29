#!/usr/bin/env python3
"""Watch audio streams for received silence and notify via Telegram."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import math
import os
import shutil
import signal
import ssl
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from array import array
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


APP_NAME = "WatchFrog"
CHUNK_SECONDS = 0.1
LOGGER = logging.getLogger("watchfrog")


def default_config_path() -> Path:
    override = os.environ.get("WATCHFROG_CONFIG")
    if override:
        return Path(override).expanduser()

    portable = Path(__file__).resolve().with_name("config.toml")
    if portable.exists():
        return portable

    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "watchfrog" / "config.toml"


def find_example_config() -> Path:
    candidates = [
        Path(__file__).resolve().with_name("config.example.toml"),
        Path(sys.prefix) / "share" / "watchfrog" / "config.example.toml",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError(
        "config.example.toml was not found. "
        "Please use the complete application package."
    )


def build_ssl_context() -> ssl.SSLContext:
    """Build a verified TLS context, including the common python.org/macOS fix."""
    verify_paths = ssl.get_default_verify_paths()
    if verify_paths.cafile and Path(verify_paths.cafile).is_file():
        return ssl.create_default_context()

    candidates = [
        Path("/etc/ssl/cert.pem"),
        Path("/opt/homebrew/etc/openssl@3/cert.pem"),
        Path("/usr/local/etc/openssl@3/cert.pem"),
    ]
    try:
        import certifi

        candidates.insert(0, Path(certifi.where()))
    except ImportError:
        pass
    for candidate in candidates:
        if candidate.is_file():
            return ssl.create_default_context(cafile=str(candidate))
    return ssl.create_default_context()


SSL_CONTEXT = build_ssl_context()


@dataclass(frozen=True)
class MonitorConfig:
    silence_seconds: float
    reception_outage_seconds: float
    silence_threshold_db: float
    recovery_seconds: float
    reconnect_delay_seconds: float
    timezone: ZoneInfo
    notify_recovery: bool
    startup_message: bool
    ffmpeg_path: str
    sample_rate: int


@dataclass(frozen=True)
class StreamConfig:
    name: str
    url: str
    silence_seconds: float
    reception_outage_seconds: float
    recovery_seconds: float
    reconnect_delay_seconds: float


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    chat_id: str

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)


@dataclass(frozen=True)
class HealthchecksConfig:
    ping_url: str
    interval_seconds: float = 60.0

    @property
    def enabled(self) -> bool:
        return bool(self.ping_url)


@dataclass(frozen=True)
class AppConfig:
    monitor: MonitorConfig
    telegram: TelegramConfig
    healthchecks: HealthchecksConfig
    streams: dict[str, StreamConfig]


def _required_table(document: dict[str, Any], name: str) -> dict[str, Any]:
    value = document.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"Configuration section [{name}] is missing.")
    return value


def _clean_secret(value: Any, placeholder: str) -> str:
    text = str(value or "").strip()
    if text == placeholder:
        return ""
    return text


def validate_http_url(url: str, label: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{label} must be a complete HTTP or HTTPS URL.")


def load_config(path: Path) -> AppConfig:
    try:
        with path.open("rb") as handle:
            document = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ValueError(
            f"Configuration not found: {path}\n"
            "Please run the setup assistant with --configure first."
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Invalid TOML configuration in {path}: {exc}") from exc

    monitor_data = _required_table(document, "monitor")
    telegram_data = _required_table(document, "telegram")
    healthchecks_data = document.get("healthchecks", {})
    if not isinstance(healthchecks_data, dict):
        raise ValueError("Configuration section [healthchecks] is invalid.")
    streams_data = _required_table(document, "streams")
    stream_overrides_data = document.get("stream_overrides", {})
    if not isinstance(stream_overrides_data, dict):
        raise ValueError("Configuration section [stream_overrides] is invalid.")

    try:
        silence_seconds = float(monitor_data.get("silence_seconds", 5.0))
        reception_outage_seconds = float(
            monitor_data.get("reception_outage_seconds", 600.0)
        )
        silence_threshold_db = float(
            monitor_data.get("silence_threshold_db", -60.0)
        )
        recovery_seconds = float(monitor_data.get("recovery_seconds", 0.5))
        reconnect_delay_seconds = float(
            monitor_data.get("reconnect_delay_seconds", 1.0)
        )
        sample_rate = int(monitor_data.get("sample_rate", 8000))
        timezone_name = str(monitor_data.get("timezone", "Europe/Berlin"))
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            f"Timezone {timezone_name!r} is not available."
        ) from exc
    except (TypeError, ValueError, KeyError) as exc:
        raise ValueError(f"Invalid value in section [monitor]: {exc}") from exc

    if silence_seconds <= 0:
        raise ValueError("silence_seconds must be greater than 0.")
    if reception_outage_seconds <= 0:
        raise ValueError("reception_outage_seconds must be greater than 0.")
    if not -120.0 <= silence_threshold_db <= 0.0:
        raise ValueError("silence_threshold_db must be between -120 and 0.")
    if recovery_seconds <= 0:
        raise ValueError("recovery_seconds must be greater than 0.")
    if reconnect_delay_seconds < 0:
        raise ValueError("reconnect_delay_seconds must not be negative.")
    if not 1000 <= sample_rate <= 48000:
        raise ValueError("sample_rate must be between 1000 and 48000.")

    streams: dict[str, StreamConfig] = {}
    for name, url in streams_data.items():
        clean_name = str(name).strip()
        clean_url = str(url).strip()
        parsed = urllib.parse.urlparse(clean_url)
        if not clean_name or parsed.scheme not in {"http", "https"}:
            raise ValueError(f"Invalid stream entry: {name!r} = {url!r}")
        override = stream_overrides_data.get(clean_name, {})
        if not isinstance(override, dict):
            raise ValueError(
                f"Timing override for {clean_name!r} must be a table."
            )
        unknown_keys = set(override) - {
            "silence_seconds",
            "reception_outage_seconds",
            "recovery_seconds",
            "reconnect_delay_seconds",
        }
        if unknown_keys:
            unknown = ", ".join(sorted(str(key) for key in unknown_keys))
            raise ValueError(
                f"Unknown timing override for {clean_name!r}: {unknown}"
            )
        try:
            stream_silence_seconds = float(
                override.get("silence_seconds", silence_seconds)
            )
            stream_reception_outage_seconds = float(
                override.get(
                    "reception_outage_seconds",
                    reception_outage_seconds,
                )
            )
            stream_recovery_seconds = float(
                override.get("recovery_seconds", recovery_seconds)
            )
            stream_reconnect_delay_seconds = float(
                override.get(
                    "reconnect_delay_seconds",
                    reconnect_delay_seconds,
                )
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Invalid timing override for {clean_name!r}: {exc}"
            ) from exc
        if stream_silence_seconds <= 0:
            raise ValueError(
                f"{clean_name}: silence_seconds must be greater than 0."
            )
        if stream_reception_outage_seconds <= 0:
            raise ValueError(
                f"{clean_name}: reception_outage_seconds must be greater than 0."
            )
        if stream_recovery_seconds <= 0:
            raise ValueError(
                f"{clean_name}: recovery_seconds must be greater than 0."
            )
        if stream_reconnect_delay_seconds < 0:
            raise ValueError(
                f"{clean_name}: reconnect_delay_seconds must not be negative."
            )
        streams[clean_name] = StreamConfig(
            name=clean_name,
            url=clean_url,
            silence_seconds=stream_silence_seconds,
            reception_outage_seconds=stream_reception_outage_seconds,
            recovery_seconds=stream_recovery_seconds,
            reconnect_delay_seconds=stream_reconnect_delay_seconds,
        )
    if not streams:
        raise ValueError("No stream is configured in [streams].")
    unknown_streams = set(stream_overrides_data) - set(streams)
    if unknown_streams:
        names = ", ".join(sorted(str(name) for name in unknown_streams))
        raise ValueError(
            "Timing override refers to unknown streams: " + names
        )

    token = os.environ.get("WATCHFROG_TELEGRAM_BOT_TOKEN") or _clean_secret(
        telegram_data.get("bot_token"),
        "INSERT_BOT_TOKEN_HERE",
    )
    chat_id = os.environ.get("WATCHFROG_TELEGRAM_CHAT_ID") or _clean_secret(
        telegram_data.get("chat_id"),
        "INSERT_CHAT_ID_HERE",
    )
    healthchecks_url = (
        os.environ.get("WATCHFROG_HEALTHCHECKS_URL")
        or _clean_secret(
            healthchecks_data.get("ping_url"),
            "INSERT_HEALTHCHECKS_URL_HERE",
        )
    )
    token = token.strip()
    chat_id = chat_id.strip()
    healthchecks_url = healthchecks_url.strip()
    if healthchecks_url:
        validate_http_url(healthchecks_url, "Healthchecks.io ping URL")

    return AppConfig(
        monitor=MonitorConfig(
            silence_seconds=silence_seconds,
            reception_outage_seconds=reception_outage_seconds,
            silence_threshold_db=silence_threshold_db,
            recovery_seconds=recovery_seconds,
            reconnect_delay_seconds=reconnect_delay_seconds,
            timezone=timezone,
            notify_recovery=bool(monitor_data.get("notify_recovery", True)),
            startup_message=bool(monitor_data.get("startup_message", True)),
            ffmpeg_path=str(monitor_data.get("ffmpeg_path", "ffmpeg")),
            sample_rate=sample_rate,
        ),
        telegram=TelegramConfig(bot_token=token, chat_id=chat_id),
        healthchecks=HealthchecksConfig(ping_url=healthchecks_url),
        streams=streams,
    )


def resolve_ffmpeg(configured_path: str) -> str:
    expanded = os.path.expanduser(configured_path)
    if os.path.sep in expanded:
        candidate = Path(expanded)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
        raise ValueError(f"ffmpeg is not executable: {configured_path}")
    found = shutil.which(expanded)
    if not found:
        raise ValueError(
            "ffmpeg was not found. Please install ffmpeg and add it to PATH "
            "or configure ffmpeg_path."
        )
    return found


def resolve_playlist(url: str, timeout: float) -> str:
    """Resolve the first playable URL from an m3u playlist."""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "WatchFrog/2.0 (+local monitoring)"},
    )
    with urllib.request.urlopen(
        request, timeout=timeout, context=SSL_CONTEXT
    ) as response:
        content = response.read(256 * 1024)
        final_url = response.geturl()

    if not url.lower().split("?", 1)[0].endswith((".m3u", ".m3u8")):
        return final_url

    text = content.decode("utf-8-sig", errors="replace")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        candidate = urllib.parse.urljoin(final_url, line)
        if urllib.parse.urlparse(candidate).scheme in {"http", "https"}:
            return candidate
    raise RuntimeError(f"Playlist contains no stream URL: {url}")


def telegram_api(
    token: str,
    method: str,
    parameters: dict[str, str] | None = None,
    *,
    timeout: float = 10.0,
) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    encoded = urllib.parse.urlencode(parameters or {}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        headers={"User-Agent": "WatchFrog/2.0"},
    )
    try:
        with urllib.request.urlopen(
            request, timeout=timeout, context=SSL_CONTEXT
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Telegram is unreachable: {exc}") from exc
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram error: {payload}")
    return payload


def send_healthcheck_ping(url: str, *, timeout: float = 10.0) -> None:
    validate_http_url(url, "Healthchecks.io ping URL")
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "WatchFrog/2.0"},
    )
    try:
        with urllib.request.urlopen(
            request, timeout=timeout, context=SSL_CONTEXT
        ) as response:
            response.read(1024)
            if response.status != 200:
                raise RuntimeError(f"HTTP status {response.status}")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Healthchecks.io HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Healthchecks.io is unreachable: {exc}") from exc


class Notifier:
    def __init__(self, telegram: TelegramConfig, disabled: bool = False) -> None:
        self.telegram = telegram
        self.disabled = disabled
        self.queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.task: asyncio.Task[None] | None = None

    @property
    def active(self) -> bool:
        return self.telegram.enabled and not self.disabled

    def start(self) -> None:
        self.task = asyncio.create_task(self._worker(), name="telegram-notifier")

    def enqueue(self, message: str) -> None:
        if not self.active:
            LOGGER.warning("Notification (Telegram inactive):\n%s", message)
            return
        self.queue.put_nowait(message)

    async def _worker(self) -> None:
        while True:
            message = await self.queue.get()
            if message is None:
                self.queue.task_done()
                return
            delay = 1.0
            for attempt in range(1, 6):
                try:
                    await asyncio.to_thread(
                        telegram_api,
                        self.telegram.bot_token,
                        "sendMessage",
                        {
                            "chat_id": self.telegram.chat_id,
                            "text": message,
                            "disable_web_page_preview": "true",
                        },
                    )
                    LOGGER.info("Telegram message sent.")
                    break
                except Exception as exc:  # network errors must not stop monitoring
                    LOGGER.error(
                        "Telegram attempt %d/5 failed: %s", attempt, exc
                    )
                    if attempt < 5:
                        await asyncio.sleep(delay)
                        delay *= 2
            self.queue.task_done()

    async def stop(self) -> None:
        if self.task is None:
            return
        try:
            await asyncio.wait_for(self.queue.join(), timeout=10.0)
        except asyncio.TimeoutError:
            LOGGER.warning("Telegram queue was not empty during shutdown.")
        self.queue.put_nowait(None)
        await self.task


class StreamState:
    def __init__(
        self,
        stream: StreamConfig,
        config: MonitorConfig,
        notifier: Notifier,
    ) -> None:
        self.name = stream.name
        self.stream = stream
        self.config = config
        self.notifier = notifier
        self.last_pcm_mono: float | None = None
        self.last_pcm_wall: datetime | None = None
        self.reception_missing_since_mono: float | None = None
        self.reception_missing_since_wall: datetime | None = None
        self.reception_outage_alerted = False
        self.outage_started_wall: datetime | None = None
        self.silence_started_wall: datetime | None = None
        self.silent_audio_seconds = 0.0
        self.recovery_audio_seconds = 0.0

    @property
    def is_outage(self) -> bool:
        return self.outage_started_wall is not None

    def observe_pcm(self) -> None:
        now_mono = time.monotonic()
        now_wall = datetime.now(self.config.timezone)
        if self.reception_outage_alerted:
            assert self.reception_missing_since_mono is not None
            outage_duration = max(
                0.0,
                now_mono - self.reception_missing_since_mono,
            )
            message = (
                "🟢 WatchFrog – Stream reception recovered\n"
                f"Stream: {self.name}\n"
                f"Recovered: {format_timestamp(now_wall)}\n"
                f"Reception outage duration: {format_duration(outage_duration)}"
            )
            LOGGER.info(
                "%s: stream reception recovered after %.1f s",
                self.name,
                outage_duration,
            )
            if self.config.notify_recovery:
                self.notifier.enqueue(message)
        self.reception_missing_since_mono = None
        self.reception_missing_since_wall = None
        self.reception_outage_alerted = False
        self.last_pcm_mono = now_mono
        self.last_pcm_wall = now_wall

    def mark_reception_missing(self) -> None:
        if self.reception_missing_since_mono is not None:
            return
        now_mono = time.monotonic()
        now_wall = datetime.now(self.config.timezone)
        self.reception_missing_since_mono = (
            self.last_pcm_mono
            if self.last_pcm_mono is not None
            else now_mono
        )
        self.reception_missing_since_wall = (
            self.last_pcm_wall
            if self.last_pcm_wall is not None
            else now_wall
        )

    def reception_alert_delay(self) -> float | None:
        if self.reception_outage_alerted:
            return None
        self.mark_reception_missing()
        assert self.reception_missing_since_mono is not None
        elapsed = time.monotonic() - self.reception_missing_since_mono
        return max(0.0, self.stream.reception_outage_seconds - elapsed)

    def check_reception_outage(self) -> None:
        if self.reception_outage_alerted:
            return
        self.mark_reception_missing()
        assert self.reception_missing_since_mono is not None
        assert self.reception_missing_since_wall is not None
        now_mono = time.monotonic()
        elapsed = max(0.0, now_mono - self.reception_missing_since_mono)
        if elapsed + 1e-9 < self.stream.reception_outage_seconds:
            return

        now_wall = datetime.now(self.config.timezone)
        self.reception_outage_alerted = True
        message = (
            "🔴 WatchFrog – Stream reception lost\n"
            f"Stream: {self.name}\n"
            f"Started: {format_timestamp(self.reception_missing_since_wall)}\n"
            f"Detected: {format_timestamp(now_wall)}\n"
            f"No received audio for at least: "
            f"{format_duration(self.stream.reception_outage_seconds)}\n"
            "Reason: No decodable audio data has been received; "
            "reconnect attempts continue."
        )
        LOGGER.error(
            "%s: no received audio data for %.1f s",
            self.name,
            elapsed,
        )
        self.notifier.enqueue(message)

    def observe_silence(self, duration: float) -> None:
        self.recovery_audio_seconds = 0.0
        if self.is_outage:
            return

        now_wall = datetime.now(self.config.timezone)
        if self.silence_started_wall is None:
            self.silence_started_wall = now_wall - timedelta(seconds=duration)
        self.silent_audio_seconds += duration
        if (
            self.silent_audio_seconds + 1e-9
            < self.stream.silence_seconds
        ):
            return

        self.outage_started_wall = self.silence_started_wall
        message = (
            "🔴 WatchFrog – Audio silence detected\n"
            f"Stream: {self.name}\n"
            f"Started: {format_timestamp(self.outage_started_wall)}\n"
            f"Detected: {format_timestamp(now_wall)}\n"
            f"Duration below threshold: at least "
            f"{format_decimal(self.stream.silence_seconds)} seconds\n"
            f"Reason: The stream is being received, but the audio level is below "
            f"{self.config.silence_threshold_db:g} dBFS"
        )
        LOGGER.error(
            "%s: received silence below %g dBFS",
            self.name,
            self.config.silence_threshold_db,
        )
        self.notifier.enqueue(message)

    def observe_sound(self, duration: float) -> None:
        now_wall = datetime.now(self.config.timezone)
        self.silence_started_wall = None
        self.silent_audio_seconds = 0.0

        if not self.is_outage:
            self.recovery_audio_seconds = 0.0
            return
        self.recovery_audio_seconds += duration
        if self.recovery_audio_seconds + 1e-9 < self.stream.recovery_seconds:
            return

        assert self.outage_started_wall is not None
        outage_duration = max(
            0.0, (now_wall - self.outage_started_wall).total_seconds()
        )
        message = (
            "🟢 WatchFrog – Audio recovered\n"
            f"Stream: {self.name}\n"
            f"Recovered: {format_timestamp(now_wall)}\n"
            f"Outage duration: {format_duration(outage_duration)}"
        )
        LOGGER.info("%s: audio recovered after %.1f s", self.name, outage_duration)
        if self.config.notify_recovery:
            self.notifier.enqueue(message)
        self.outage_started_wall = None
        self.recovery_audio_seconds = 0.0

    def reset_silence_candidate(self) -> None:
        """Do not combine silence periods across reception gaps."""
        self.recovery_audio_seconds = 0.0
        if not self.is_outage:
            self.silence_started_wall = None
            self.silent_audio_seconds = 0.0


def format_decimal(value: float) -> str:
    return f"{value:.1f}"


def format_timestamp(value: datetime) -> str:
    zone = value.tzname() or ""
    return f"{value:%Y-%m-%d %H:%M:%S} {zone}".strip()


def format_duration(seconds: float) -> str:
    rounded = max(0, round(seconds))
    hours, remainder = divmod(rounded, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d} h {minutes:02d} min {secs:02d} s"
    if minutes:
        return f"{minutes:d} min {secs:02d} s"
    return f"{secs:d} s"


def pcm_dbfs(chunk: bytes) -> float:
    samples = array("h")
    samples.frombytes(chunk)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return -math.inf
    mean_square = sum(sample * sample for sample in samples) / len(samples)
    if mean_square <= 0:
        return -math.inf
    rms = math.sqrt(mean_square)
    return 20.0 * math.log10(rms / 32768.0)


async def consume_stderr(
    stream_name: str, stream: asyncio.StreamReader
) -> None:
    while line := await stream.readline():
        message = line.decode("utf-8", errors="replace").strip()
        if message:
            LOGGER.warning("%s / ffmpeg: %s", stream_name, message)


async def terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


async def decode_stream_once(
    stream: StreamConfig,
    state: StreamState,
    config: MonitorConfig,
    ffmpeg_path: str,
    stop_event: asyncio.Event,
) -> None:
    state.reset_silence_candidate()
    resolve_timeout = max(3.0, stream.silence_seconds)
    resolved_url = await asyncio.wait_for(
        asyncio.to_thread(resolve_playlist, stream.url, resolve_timeout),
        timeout=resolve_timeout + 0.5,
    )
    LOGGER.info("%s: playlist resolved", stream.name)

    arguments = [
        ffmpeg_path,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "warning",
        "-reconnect",
        "1",
        "-reconnect_streamed",
        "1",
        "-reconnect_delay_max",
        "2",
        "-i",
        resolved_url,
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(config.sample_rate),
        "-f",
        "s16le",
        "pipe:1",
    ]
    process = await asyncio.create_subprocess_exec(
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    stderr_task = asyncio.create_task(
        consume_stderr(stream.name, process.stderr),
        name=f"{stream.name}-ffmpeg-stderr",
    )

    bytes_per_chunk = round(config.sample_rate * 2 * CHUNK_SECONDS)
    buffer = bytearray()
    last_output_mono = time.monotonic()
    stall_restart_seconds = max(5.0, stream.silence_seconds + 2.0)
    try:
        while not stop_event.is_set():
            try:
                data = await asyncio.wait_for(process.stdout.read(4096), timeout=0.5)
            except asyncio.TimeoutError:
                if process.returncode is not None:
                    break
                buffer.clear()
                state.reset_silence_candidate()
                state.mark_reception_missing()
                state.check_reception_outage()
                if time.monotonic() - last_output_mono >= stall_restart_seconds:
                    raise RuntimeError(
                        f"ffmpeg has produced no audio data for "
                        f"{stall_restart_seconds:g} s"
                    )
                continue
            if not data:
                break
            last_output_mono = time.monotonic()
            state.observe_pcm()
            buffer.extend(data)
            while len(buffer) >= bytes_per_chunk:
                chunk = bytes(buffer[:bytes_per_chunk])
                del buffer[:bytes_per_chunk]
                level = pcm_dbfs(chunk)
                if level > config.silence_threshold_db:
                    state.observe_sound(CHUNK_SECONDS)
                else:
                    state.observe_silence(CHUNK_SECONDS)
        if not stop_event.is_set():
            return_code = await process.wait()
            raise RuntimeError(f"ffmpeg exited with code {return_code}")
    finally:
        await terminate_process(process)
        await stderr_task


async def monitor_stream(
    stream: StreamConfig,
    config: MonitorConfig,
    notifier: Notifier,
    ffmpeg_path: str,
    stop_event: asyncio.Event,
) -> None:
    state = StreamState(stream, config, notifier)
    state.mark_reception_missing()
    retry_delay = stream.reconnect_delay_seconds
    while not stop_event.is_set():
        previous_pcm_mono = state.last_pcm_mono
        try:
            await decode_stream_once(
                stream,
                state,
                config,
                ffmpeg_path,
                stop_event,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            state.reset_silence_candidate()
            LOGGER.warning(
                "%s: reception error; reconnecting: %s",
                stream.name,
                exc,
            )
            if state.last_pcm_mono != previous_pcm_mono:
                retry_delay = stream.reconnect_delay_seconds
            else:
                retry_delay = min(max(1.0, retry_delay * 2), 30.0)
        if not stop_event.is_set():
            state.check_reception_outage()
            alert_delay = state.reception_alert_delay()
            wait_timeout = retry_delay
            if alert_delay is not None:
                wait_timeout = min(wait_timeout, alert_delay)
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=wait_timeout
                )
            except asyncio.TimeoutError:
                pass
            state.check_reception_outage()


async def healthcheck_loop(
    config: HealthchecksConfig,
    monitor_tasks: list[asyncio.Task[None]],
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        if all(not task.done() for task in monitor_tasks):
            try:
                await asyncio.to_thread(send_healthcheck_ping, config.ping_url)
                LOGGER.debug("Healthchecks.io heartbeat sent.")
            except Exception as exc:
                LOGGER.warning("Healthchecks.io heartbeat failed: %s", exc)
        else:
            LOGGER.error(
                "Healthchecks.io heartbeat suspended: "
                "at least one stream monitor is no longer running."
            )
        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=config.interval_seconds
            )
        except asyncio.TimeoutError:
            pass


async def run_monitor(
    config: AppConfig,
    *,
    notifications_disabled: bool = False,
    run_seconds: float | None = None,
) -> None:
    ffmpeg_path = resolve_ffmpeg(config.monitor.ffmpeg_path)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    notifier = Notifier(config.telegram, disabled=notifications_disabled)
    notifier.start()
    if not notifier.active:
        LOGGER.warning(
            "Telegram is not configured or has been disabled; "
            "alerts will only appear in the log."
        )

    LOGGER.info(
        "%s starting: %d streams, silence %.1f s / %.1f dBFS, "
        "reception outage %.1f s, ffmpeg %s",
        APP_NAME,
        len(config.streams),
        config.monitor.silence_seconds,
        config.monitor.silence_threshold_db,
        config.monitor.reception_outage_seconds,
        ffmpeg_path,
    )
    for stream in config.streams.values():
        if (
            stream.silence_seconds != config.monitor.silence_seconds
            or stream.reception_outage_seconds
            != config.monitor.reception_outage_seconds
            or stream.recovery_seconds != config.monitor.recovery_seconds
            or stream.reconnect_delay_seconds
            != config.monitor.reconnect_delay_seconds
        ):
            LOGGER.info(
                "%s: custom timings silence %.1f s, reception outage %.1f s, "
                "recovery %.1f s, reconnect %.1f s",
                stream.name,
                stream.silence_seconds,
                stream.reception_outage_seconds,
                stream.recovery_seconds,
                stream.reconnect_delay_seconds,
            )
    if config.monitor.startup_message:
        notifier.enqueue(
            "✅ WatchFrog started\n"
            f"Monitored streams: {len(config.streams)}\n"
            "Default alert threshold: "
            f"{format_decimal(config.monitor.silence_seconds)} seconds "
            "of received silence\n"
            "Reception outage alert: "
            f"{format_duration(config.monitor.reception_outage_seconds)} "
            "without received audio\n"
            f"Time: {format_timestamp(datetime.now(config.monitor.timezone))}"
        )

    tasks = [
        asyncio.create_task(
            monitor_stream(
                stream,
                config.monitor,
                notifier,
                ffmpeg_path,
                stop_event,
            ),
            name=stream.name,
        )
        for stream in config.streams.values()
    ]
    healthcheck_task: asyncio.Task[None] | None = None
    if config.healthchecks.enabled:
        healthcheck_task = asyncio.create_task(
            healthcheck_loop(config.healthchecks, tasks, stop_event),
            name="healthchecks-heartbeat",
        )
        LOGGER.info("Healthchecks.io heartbeat is active (every 60 seconds).")
    else:
        LOGGER.info("Healthchecks.io heartbeat is not configured.")

    timer: asyncio.Task[None] | None = None
    if run_seconds is not None:
        async def stop_later() -> None:
            await asyncio.sleep(run_seconds)
            stop_event.set()

        timer = asyncio.create_task(stop_later(), name="test-timer")

    await stop_event.wait()
    LOGGER.info("%s shutting down.", APP_NAME)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    if healthcheck_task is not None:
        healthcheck_task.cancel()
        await asyncio.gather(healthcheck_task, return_exceptions=True)
    if timer is not None:
        timer.cancel()
        await asyncio.gather(timer, return_exceptions=True)
    await notifier.stop()


def setup_logging(log_file: Path | None, verbose: bool) -> None:
    handlers: list[logging.Handler] = []
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    if log_file is not None:
        from logging.handlers import RotatingFileHandler

        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_file,
                maxBytes=2_000_000,
                backupCount=3,
                encoding="utf-8",
            )
        )
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def choose_chat(token: str) -> str:
    input(
        "\nPlease send any message to the new bot in Telegram now.\n"
        "Then press Enter here … "
    )
    payload = telegram_api(token, "getUpdates", {"timeout": "0"})
    chats: dict[str, str] = {}
    for update in payload.get("result", []):
        message = (
            update.get("message")
            or update.get("channel_post")
            or update.get("edited_message")
        )
        if not isinstance(message, dict):
            continue
        chat = message.get("chat")
        if not isinstance(chat, dict) or "id" not in chat:
            continue
        chat_id = str(chat["id"])
        label = (
            chat.get("title")
            or " ".join(
                part
                for part in (chat.get("first_name"), chat.get("last_name"))
                if part
            )
            or chat.get("username")
            or chat_id
        )
        chats[chat_id] = str(label)
    if not chats:
        raise RuntimeError(
            "No message found. Please send a message to the bot in Telegram "
            "and restart the setup."
        )
    if len(chats) == 1:
        chat_id, label = next(iter(chats.items()))
        print(f"Telegram chat found: {label}")
        return chat_id

    print("\nTelegram chats found:")
    choices = list(chats.items())
    for index, (_, label) in enumerate(choices, start=1):
        print(f"  {index}. {label}")
    while True:
        answer = input("Number of the desired chat: ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(choices):
            return choices[int(answer) - 1][0]
        print("Please enter a valid number.")


def choose_streams() -> dict[str, str]:
    print("\nAudio stream setup")
    print("Please enter at least one name and its stream URL.")
    streams: dict[str, str] = {}
    while True:
        name = input("Stream name: ").strip()
        if not name:
            print("Please enter a stream name.")
            continue
        url = input("Stream URL: ").strip()
        try:
            validate_http_url(url, "Stream URL")
        except ValueError as exc:
            print(exc)
            continue
        streams[name] = url
        answer = input("Add another stream? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            return streams


def configure_interactively(config_path: Path) -> None:
    example_config = find_example_config()
    print("\nTelegram setup for WatchFrog")
    print("You can obtain the bot token from @BotFather in Telegram.")
    token = getpass.getpass("Bot token (input is hidden): ").strip()
    if not token:
        raise RuntimeError("No bot token was entered.")
    bot_info = telegram_api(token, "getMe")
    username = bot_info.get("result", {}).get("username", "Telegram bot")
    print(f"Bot confirmed: @{username}")
    chat_id = choose_chat(token)
    healthchecks_url = input(
        "\nHealthchecks.io ping URL "
        "(optional, leave empty to skip): "
    ).strip()
    if healthchecks_url:
        validate_http_url(healthchecks_url, "Healthchecks.io ping URL")
        send_healthcheck_ping(healthchecks_url)
        print("Healthchecks.io test ping succeeded.")
    streams = choose_streams()
    stream_lines = "\n".join(
        f"{json.dumps(name, ensure_ascii=False)} = {json.dumps(url)}"
        for name, url in streams.items()
    )

    template = example_config.read_text(encoding="utf-8")
    configured = template.replace(
        '"INSERT_BOT_TOKEN_HERE"', json.dumps(token)
    ).replace(
        '"INSERT_CHAT_ID_HERE"', json.dumps(chat_id)
    ).replace(
        '"INSERT_HEALTHCHECKS_URL_HERE"', json.dumps(healthchecks_url)
    ).replace(
        '"Example Stream" = "https://example.com/audio-stream.m3u"',
        stream_lines,
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(configured, encoding="utf-8")
    config_path.chmod(0o600)
    print(f"Configuration saved: {config_path}")


def set_healthcheck_url(config_path: Path, ping_url: str) -> None:
    lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    section_start: int | None = None
    section_end = len(lines)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "[healthchecks]":
            section_start = index
            continue
        if section_start is not None and stripped.startswith("["):
            section_end = index
            break

    setting = f"ping_url = {json.dumps(ping_url)}\n"
    if section_start is None:
        insert_at = next(
            (
                index
                for index, line in enumerate(lines)
                if line.strip() == "[streams]"
            ),
            len(lines),
        )
        lines[insert_at:insert_at] = ["[healthchecks]\n", setting, "\n"]
    else:
        for index in range(section_start + 1, section_end):
            if lines[index].lstrip().startswith("ping_url"):
                lines[index] = setting
                break
        else:
            lines.insert(section_start + 1, setting)

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("".join(lines), encoding="utf-8")
    config_path.chmod(0o600)


def configure_healthcheck_interactively(config_path: Path) -> None:
    config = load_config(config_path)
    if config.healthchecks.enabled:
        prompt = (
            "\nHealthchecks.io is already configured.\n"
            "New ping URL (empty = unchanged, - = remove): "
        )
    else:
        prompt = (
            "\nHealthchecks.io ping URL "
            "(optional, leave empty to skip): "
        )
    answer = input(prompt).strip()
    if not answer:
        print("Healthchecks.io setting remains unchanged.")
        return
    if answer == "-":
        set_healthcheck_url(config_path, "")
        print("Healthchecks.io heartbeat has been disabled.")
        return
    validate_http_url(answer, "Healthchecks.io ping URL")
    send_healthcheck_ping(answer)
    set_healthcheck_url(config_path, answer)
    print("Healthchecks.io test ping succeeded; URL was saved.")


def test_telegram(config: AppConfig) -> None:
    if not config.telegram.enabled:
        raise RuntimeError("Telegram is not fully configured yet.")
    now = datetime.now(config.monitor.timezone)
    telegram_api(
        config.telegram.bot_token,
        "sendMessage",
        {
            "chat_id": config.telegram.chat_id,
            "text": (
                "✅ WatchFrog: Telegram test succeeded\n"
                f"Time: {format_timestamp(now)}"
            ),
        },
    )
    print("Telegram test message sent.")


def test_healthcheck(config: AppConfig) -> None:
    if not config.healthchecks.enabled:
        print("Healthchecks.io is not configured; test skipped.")
        return
    send_healthcheck_ping(config.healthchecks.ping_url)
    print("Healthchecks.io test ping succeeded.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=default_config_path(),
        help="path to the TOML configuration",
    )
    parser.add_argument(
        "--configure",
        action="store_true",
        help="configure Telegram and Healthchecks.io interactively",
    )
    parser.add_argument(
        "--configure-healthcheck",
        action="store_true",
        help="configure the Healthchecks.io ping URL interactively",
    )
    parser.add_argument(
        "--test-telegram",
        action="store_true",
        help="send a Telegram test message",
    )
    parser.add_argument(
        "--test-healthcheck",
        action="store_true",
        help="send a Healthchecks.io test ping",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="check the configuration and ffmpeg",
    )
    parser.add_argument(
        "--run-seconds",
        type=float,
        help="run for a limited time for functional testing",
    )
    parser.add_argument(
        "--no-notifications",
        action="store_true",
        help="disable Telegram during functional testing",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging(
        args.config.expanduser().resolve().parent / "logs" / "watchfrog.log",
        verbose=args.verbose,
    )
    try:
        if args.configure:
            configure_interactively(args.config)
            return 0
        if args.configure_healthcheck:
            configure_healthcheck_interactively(args.config)
            return 0
        config = load_config(args.config)
        ffmpeg_path = resolve_ffmpeg(config.monitor.ffmpeg_path)
        if args.check:
            telegram_status = (
                "configured" if config.telegram.enabled else "not configured"
            )
            healthchecks_status = (
                "configured"
                if config.healthchecks.enabled
                else "not configured"
            )
            print(
                f"Configuration valid: {len(config.streams)} streams, "
                f"ffmpeg={ffmpeg_path}, Telegram={telegram_status}, "
                f"Healthchecks.io={healthchecks_status}"
            )
            return 0
        if args.test_telegram:
            test_telegram(config)
            return 0
        if args.test_healthcheck:
            test_healthcheck(config)
            return 0
        if args.run_seconds is not None and args.run_seconds <= 0:
            raise ValueError("--run-seconds must be greater than 0.")
        asyncio.run(
            run_monitor(
                config,
                notifications_disabled=args.no_notifications,
                run_seconds=args.run_seconds,
            )
        )
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        LOGGER.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
