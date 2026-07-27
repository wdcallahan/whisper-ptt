from __future__ import annotations

import signal
import subprocess
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable

from .config import AudioConfig


class RecorderError(RuntimeError):
    """Raised when PipeWire capture cannot produce a valid WAV file."""


@dataclass
class Recording:
    audio_path: Path
    stderr_path: Path
    process: subprocess.Popen[bytes]
    stderr_stream: BinaryIO
    started_at: float

    @property
    def duration_ms(self) -> int:
        return round((time.monotonic() - self.started_at) * 1000)


class PipeWireRecorder:
    def __init__(
        self,
        config: AudioConfig,
        popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    ) -> None:
        self.config = config
        self._popen = popen

    def command(self, audio_path: Path) -> list[str]:
        return [
            self.config.recorder,
            f"--target={self.config.source}",
            f"--rate={self.config.rate}",
            f"--channels={self.config.channels}",
            "--channel-map=mono",
            f"--format={self.config.sample_format}",
            "--media-role=Communication",
            str(audio_path),
        ]

    def start(self, utterance_dir: Path) -> Recording:
        utterance_dir.mkdir(parents=True, exist_ok=False)
        audio_path = utterance_dir / "audio.wav"
        stderr_path = utterance_dir / "pw-record.stderr"
        stderr_stream = stderr_path.open("wb")
        try:
            process = self._popen(
                self.command(audio_path),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr_stream,
                start_new_session=True,
            )
        except OSError as error:
            stderr_stream.close()
            raise RecorderError(f"failed to start pw-record: {error}") from error
        except Exception:
            stderr_stream.close()
            raise

        recording = Recording(
            audio_path=audio_path,
            stderr_path=stderr_path,
            process=process,
            stderr_stream=stderr_stream,
            started_at=time.monotonic(),
        )
        time.sleep(0.05)
        return_code = process.poll()
        if return_code is not None:
            stderr_stream.close()
            raise RecorderError(
                f"pw-record exited immediately with status {return_code}; "
                f"see {stderr_path}"
            )
        return recording

    def stop(self, recording: Recording) -> Path:
        process = recording.process
        sent_interrupt = False
        if process.poll() is None:
            sent_interrupt = True
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=self.config.stop_timeout_seconds)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
        recording.stderr_stream.close()

        return_code = process.returncode
        accepted_return = return_code in (
            0,
            -signal.SIGINT,
            128 + signal.SIGINT,
        ) or (sent_interrupt and return_code == 1)
        if not accepted_return:
            raise RecorderError(
                f"pw-record exited with status {return_code}; "
                f"see {recording.stderr_path}"
            )
        self._validate_wav(recording)
        return recording.audio_path

    def _validate_wav(self, recording: Recording) -> None:
        path = recording.audio_path
        if not path.is_file() or path.stat().st_size <= 44:
            raise RecorderError(
                f"pw-record did not create a nonempty WAV file; "
                f"see {recording.stderr_path}"
            )
        try:
            with wave.open(str(path), "rb") as audio:
                channels = audio.getnchannels()
                rate = audio.getframerate()
                sample_width = audio.getsampwidth()
                frame_count = audio.getnframes()
                compression = audio.getcomptype()
                payload = audio.readframes(frame_count)
        except (EOFError, OSError, wave.Error) as error:
            raise RecorderError(
                f"pw-record created an invalid WAV file: {error}; "
                f"see {recording.stderr_path}"
            ) from error

        expected_payload = frame_count * channels * sample_width
        problems: list[str] = []
        if channels != self.config.channels:
            problems.append(f"channels={channels}")
        if rate != self.config.rate:
            problems.append(f"rate={rate}")
        if sample_width != 2:
            problems.append(f"sample_width={sample_width}")
        if compression != "NONE":
            problems.append(f"compression={compression}")
        if frame_count <= 0:
            problems.append("frames=0")
        if len(payload) != expected_payload:
            problems.append(
                f"payload_bytes={len(payload)} expected={expected_payload}"
            )
        if problems:
            raise RecorderError(
                "pw-record created a WAV outside the accepted "
                f"16 kHz mono signed-16 path ({', '.join(problems)}); "
                f"see {recording.stderr_path}"
            )

    def abort(self, recording: Recording) -> None:
        try:
            self.stop(recording)
        except RecorderError:
            pass
