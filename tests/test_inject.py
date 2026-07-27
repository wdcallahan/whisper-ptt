from __future__ import annotations

import subprocess
import unittest

from nova_whisper_ptt.config import InjectionConfig
from nova_whisper_ptt.inject import InjectionError, YdotoolInjector, normalize_text


class InjectionTests(unittest.TestCase):
    def test_normalizes_whisper_punctuation_without_rewriting_words(self) -> None:
        config = InjectionConfig()
        self.assertEqual(
            normalize_text("  “Hello”\nworld—it’s me…  ", config),
            '"Hello" world-it\'s me...',
        )

    def test_rejects_unmapped_unicode_in_ascii_proof(self) -> None:
        with self.assertRaisesRegex(InjectionError, "U\\+03B2"):
            normalize_text("beta β", InjectionConfig())

    def test_passes_text_on_stdin_with_escaping_disabled(self) -> None:
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, b"", b"")

        result = YdotoolInjector(InjectionConfig(), runner=runner).inject(
            r"-literal \n"
        )
        command, kwargs = calls[0]
        self.assertEqual(
            command,
            ["/usr/bin/ydotool", "type", "--file=-", "--escape=0"],
        )
        self.assertEqual(kwargs["input"], rb"-literal \n")
        self.assertEqual(result.character_count, 11)
