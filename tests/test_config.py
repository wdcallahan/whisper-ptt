from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nova_whisper_ptt.config import ConfigError, InputConfig, load_config


CONFIG = """
[input]
key_name = "KEY_MACRO28"
device = "/dev/input/fake"

[audio]
source = "alsa_input.test"
rate = {rate}
channels = 1
sample_format = "s16"

[whisper]
model_path = "{model}"

[injection]
ascii_only = true

[focus]
require_unchanged = true

[feedback]
notifications = false

[runtime]
state_dir = "{state}"
runtime_dir = "{runtime}"
"""


class ConfigTests(unittest.TestCase):
    def test_default_input_path_is_consumer_control(self) -> None:
        self.assertEqual(
            InputConfig().device_globs,
            ("/dev/input/by-id/usb-Keychron_Lemokey_X2-event-if01",),
        )

    def test_loads_explicit_fail_closed_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "config.toml"
            path.write_text(
                CONFIG.format(
                    rate=16000,
                    model=root / "model.bin",
                    state=root / "state",
                    runtime=root / "runtime",
                ),
                encoding="utf-8",
            )
            config = load_config(path)
            self.assertEqual(config.audio.source, "alsa_input.test")
            self.assertEqual(config.input.key_name, "KEY_MACRO28")
            self.assertEqual(config.whisper.threads, 6)
            self.assertTrue(config.focus.require_unchanged)

    def test_rejects_non_whisper_sample_rate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "config.toml"
            path.write_text(
                CONFIG.format(
                    rate=48000,
                    model=root / "model.bin",
                    state=root / "state",
                    runtime=root / "runtime",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ConfigError, "audio.rate=16000"):
                load_config(path)

    def test_rejects_string_that_looks_like_false(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "config.toml"
            path.write_text(
                CONFIG.format(
                    rate=16000,
                    model=root / "model.bin",
                    state=root / "state",
                    runtime=root / "runtime",
                )
                + 'retain_successful_audio = "false"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ConfigError, "retain_successful_audio must be a TOML boolean"
            ):
                load_config(path)
