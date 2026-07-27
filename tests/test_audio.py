from __future__ import annotations

import signal
import tempfile
import time
import unittest
import wave
from pathlib import Path

from nova_whisper_ptt.audio import PipeWireRecorder, RecorderError, Recording
from nova_whisper_ptt.config import AudioConfig


class FakeProcess:
    def __init__(self, returncode=None) -> None:
        self.returncode = returncode
        self.sent_signals = []

    def poll(self):
        return self.returncode

    def send_signal(self, sent_signal):
        self.sent_signals.append(sent_signal)
        self.returncode = 1

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.returncode = -signal.SIGTERM

    def kill(self):
        self.returncode = -signal.SIGKILL


def write_wav(
    path: Path,
    *,
    channels: int = 1,
    rate: int = 16_000,
    sample_width: int = 2,
) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(sample_width)
        audio.setframerate(rate)
        audio.writeframes(b"\0" * rate * channels * sample_width)


def recording(root: Path, process: FakeProcess) -> Recording:
    audio_path = root / "audio.wav"
    stderr_path = root / "pw-record.stderr"
    return Recording(
        audio_path=audio_path,
        stderr_path=stderr_path,
        process=process,
        stderr_stream=stderr_path.open("wb"),
        started_at=time.monotonic() - 1,
    )


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

    def test_accepts_status_one_only_after_our_interrupt_and_valid_wav(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.mkdir(exist_ok=True)
            process = FakeProcess()
            current = recording(root, process)
            write_wav(current.audio_path)

            result = PipeWireRecorder(AudioConfig()).stop(current)

            self.assertEqual(result, current.audio_path)
            self.assertEqual(process.sent_signals, [signal.SIGINT])

    def test_rejects_spontaneous_status_one_even_with_valid_wav(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.mkdir(exist_ok=True)
            current = recording(root, FakeProcess(returncode=1))
            write_wav(current.audio_path)

            with self.assertRaisesRegex(RecorderError, "status 1"):
                PipeWireRecorder(AudioConfig()).stop(current)

    def test_rejects_wrong_wav_format_after_intentional_stop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.mkdir(exist_ok=True)
            current = recording(root, FakeProcess())
            write_wav(current.audio_path, rate=48_000)

            with self.assertRaisesRegex(RecorderError, "rate=48000"):
                PipeWireRecorder(AudioConfig()).stop(current)
