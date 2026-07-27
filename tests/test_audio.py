from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nova_whisper_ptt.audio import PipeWireRecorder, RecorderError
from nova_whisper_ptt.config import AudioConfig


class AudioTests(unittest.TestCase):
    def test_recorder_command_has_explicit_source_and_whisper_format(self) -> None:
        config = AudioConfig(source="alsa_input.only-approved-microphone")
        command = PipeWireRecorder(config).command(Path("/tmp/proof.wav"))
        self.assertIn("--target=alsa_input.only-approved-microphone", command)
        self.assertIn("--rate=16000", command)
        self.assertIn("--channels=1", command)
        self.assertIn("--channel-map=mono", command)
        self.assertIn("--format=s16", command)
        self.assertNotIn("--target=auto", command)

    def test_start_error_is_reported_as_recorder_failure(self) -> None:
        def popen(*_args, **_kwargs):
            raise OSError("recorder unavailable")

        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(RecorderError, "recorder unavailable"):
                PipeWireRecorder(AudioConfig(), popen=popen).start(
                    Path(temporary) / "utterance"
                )
