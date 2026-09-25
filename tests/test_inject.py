from __future__ import annotations

import subprocess
import unittest

from nova_whisper_ptt.config import InjectionConfig
from nova_whisper_ptt.inject import InjectionError, YdotoolInjector, normalize_text


class InjectionTests(unittest.TestCase):
    def test_normalizes_whisper_punctuation_without_rewriting_words(self) -> None:
        config = InjectionConfig(trailing_space=False)
        self.assertEqual(
            normalize_text("  “Hello”\nworld—it’s me…  ", config),
            '"Hello" world-it\'s me...',
        )

    def test_default_appends_exactly_one_inter_utterance_space(self) -> None:
        config = InjectionConfig()
        normalized = normalize_text("Hello.   ", config)
        self.assertEqual(normalized, "Hello. ")
        self.assertEqual(normalize_text(normalized, config), "Hello. ")

    def test_focus_guard_stops_before_the_next_bounded_chunk(self) -> None:
        calls = []
        checks = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, b"", b"")

        def before_chunk(character_count):
            checks.append(character_count)
            if character_count >= 8:
                raise RuntimeError("focus changed")

        injector = YdotoolInjector(
            InjectionConfig(trailing_space=False), runner=runner
        )
        with self.assertRaisesRegex(RuntimeError, "focus changed"):
            injector.inject(
                "abcdefghijklmnop",
                before_chunk=before_chunk,
            )

        self.assertEqual(checks, [0, 8])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1]["input"], b"abcdefgh")

    def test_rejects_unmapped_unicode_in_ascii_proof(self) -> None:
        with self.assertRaisesRegex(InjectionError, "U\\+03B2"):
            normalize_text("beta β", InjectionConfig())

    def test_passes_text_on_stdin_with_escaping_disabled(self) -> None:
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, b"", b"")

        result = YdotoolInjector(
            InjectionConfig(trailing_space=False), runner=runner
        ).inject(
            r"-literal \n"
        )
        command, kwargs = calls[0]
        self.assertEqual(
            command,
            [
                "/usr/bin/ydotool",
                "type",
                "--key-delay=8",
                "--key-hold=8",
                "--file=-",
                "--escape=0",
            ],
        )
        self.assertEqual(kwargs["input"], rb"-literal \n")
        self.assertEqual(result.character_count, 11)
