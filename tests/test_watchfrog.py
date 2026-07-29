import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import watchfrog


class FakeNotifier:
    def __init__(self):
        self.messages = []

    def enqueue(self, message):
        self.messages.append(message)


def monitor_config():
    return watchfrog.MonitorConfig(
        silence_seconds=5.0,
        silence_threshold_db=-60.0,
        recovery_seconds=0.5,
        reconnect_delay_seconds=1.0,
        timezone=ZoneInfo("Europe/Berlin"),
        notify_recovery=True,
        startup_message=False,
        ffmpeg_path="ffmpeg",
        sample_rate=8000,
    )


def stream_config(
    name="Example Stream",
    silence_seconds=5.0,
    recovery_seconds=0.5,
    reconnect_delay_seconds=1.0,
):
    return watchfrog.StreamConfig(
        name=name,
        url="https://example.test/stream.m3u",
        silence_seconds=silence_seconds,
        recovery_seconds=recovery_seconds,
        reconnect_delay_seconds=reconnect_delay_seconds,
    )


class PcmTests(unittest.TestCase):
    def test_digital_silence(self):
        self.assertEqual(watchfrog.pcm_dbfs(b"\x00\x00" * 800), -math.inf)

    def test_known_level(self):
        samples = (1000).to_bytes(2, "little", signed=True) * 800
        expected = 20 * math.log10(1000 / 32768)
        self.assertAlmostEqual(watchfrog.pcm_dbfs(samples), expected, places=5)


class HealthcheckTests(unittest.TestCase):
    def test_healthcheck_uses_get(self):
        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return b"OK"

        with patch(
            "watchfrog.urllib.request.urlopen",
            return_value=FakeResponse(),
        ) as urlopen:
            watchfrog.send_healthcheck_ping("https://hc-ping.com/example")

        self.assertEqual(urlopen.call_args.args[0].get_method(), "GET")

    def test_invalid_healthcheck_url_is_rejected(self):
        with self.assertRaises(ValueError):
            watchfrog.send_healthcheck_ping("not-a-url")


class StreamStateTests(unittest.TestCase):
    def test_no_reception_does_not_alert(self):
        notifier = FakeNotifier()
        state = watchfrog.StreamState(
            stream_config(), monitor_config(), notifier
        )
        state.reset_silence_candidate()
        self.assertEqual(notifier.messages, [])

    def test_received_silence_alerts_and_sound_recovers(self):
        notifier = FakeNotifier()
        state = watchfrog.StreamState(
            stream_config(), monitor_config(), notifier
        )
        for _ in range(49):
            state.observe_silence(0.1)
        self.assertEqual(notifier.messages, [])
        state.observe_silence(0.1)
        self.assertIn("WatchFrog – Audioausfall", notifier.messages[0])

        for _ in range(5):
            state.observe_sound(0.1)
        self.assertIn("WatchFrog – Audio wieder da", notifier.messages[1])

    def test_reception_gap_resets_partial_silence(self):
        notifier = FakeNotifier()
        state = watchfrog.StreamState(
            stream_config(), monitor_config(), notifier
        )
        for _ in range(20):
            state.observe_silence(0.1)
        state.reset_silence_candidate()
        for _ in range(20):
            state.observe_silence(0.1)
        self.assertEqual(notifier.messages, [])

    def test_per_stream_times_are_used(self):
        notifier = FakeNotifier()
        state = watchfrog.StreamState(
            stream_config(silence_seconds=1.0, recovery_seconds=1.0),
            monitor_config(),
            notifier,
        )
        for _ in range(10):
            state.observe_silence(0.1)
        self.assertEqual(len(notifier.messages), 1)
        for _ in range(9):
            state.observe_sound(0.1)
        self.assertEqual(len(notifier.messages), 1)
        state.observe_sound(0.1)
        self.assertEqual(len(notifier.messages), 2)


class ConfigTests(unittest.TestCase):
    def test_example_config_loads(self):
        config = watchfrog.load_config(
            Path(__file__).parents[1] / "config.example.toml"
        )
        self.assertEqual(len(config.streams), 1)
        self.assertEqual(config.streams["Example Stream"].silence_seconds, 5.0)
        self.assertFalse(config.telegram.enabled)

    def test_stream_overrides_inherit_defaults(self):
        source = Path(__file__).parents[1] / "config.example.toml"
        text = source.read_text(encoding="utf-8")
        text += (
            '\n[stream_overrides."Example Stream"]\n'
            "silence_seconds = 8.0\n"
            "recovery_seconds = 1.5\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(text, encoding="utf-8")
            config = watchfrog.load_config(path)
        self.assertEqual(config.streams["Example Stream"].silence_seconds, 8.0)
        self.assertEqual(config.streams["Example Stream"].recovery_seconds, 1.5)
        self.assertEqual(
            config.streams["Example Stream"].reconnect_delay_seconds,
            1.0,
        )

    def test_unknown_override_is_rejected(self):
        source = Path(__file__).parents[1] / "config.example.toml"
        text = source.read_text(encoding="utf-8")
        text += '\n[stream_overrides."Unbekannt"]\nsilence_seconds = 8.0\n'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unbekannte Streams"):
                watchfrog.load_config(path)

    def test_default_path_uses_watchfrog_name(self):
        with patch.dict(
            os.environ,
            {"XDG_CONFIG_HOME": "/tmp/example", "WATCHFROG_CONFIG": ""},
        ):
            path = watchfrog.default_config_path()
        self.assertEqual(path, Path("/tmp/example/watchfrog/config.toml"))

if __name__ == "__main__":
    unittest.main()
