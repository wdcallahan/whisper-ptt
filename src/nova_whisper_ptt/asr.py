from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import WhisperConfig


class TranscriptionError(RuntimeError):
    """Raised when a local model cannot produce a final transcript."""


@dataclass(frozen=True)
class Transcript:
    text: str
    elapsed_seconds: float
    segment_count: int


_SQUARE_ANNOTATION = re.compile(
    r"^\[(?=[^\]\r\n]*[A-Za-z])[A-Za-z0-9 _-]{1,80}\][.!]?$"
)
_CONTROL_ANNOTATION = re.compile(
    r"^<\|(?=[^|\r\n]*[A-Za-z])[A-Za-z0-9 _-]{1,80}\|>[.!]?$"
)
_PARENTHETICAL_ANNOTATION = re.compile(r"^\(([^()\r\n]{1,80})\)[.!]?$")
_KNOWN_PARENTHETICAL_CUES = frozenset(
    {
        "applause",
        "background noise",
        "blank audio",
        "clapping",
        "cough",
        "coughing",
        "inaudible",
        "laughing",
        "laughter",
        "music",
        "no audio",
        "no speech",
        "noise",
        "silence",
        "sound effect",
        "sound effects",
        "unintelligible",
    }
)
_MUSICAL_CUE_CHARACTERS = frozenset("♪♫♬♩ ")


def classify_transcript_annotation(text: str) -> str | None:
    """Return an annotation-only transcript that must not become keystrokes."""

    candidate = re.sub(r"\s+", " ", text).strip()
    if not candidate:
        return None
    if _SQUARE_ANNOTATION.fullmatch(candidate):
        return candidate
    if _CONTROL_ANNOTATION.fullmatch(candidate):
        return candidate
    parenthetical = _PARENTHETICAL_ANNOTATION.fullmatch(candidate)
    if parenthetical:
        label = re.sub(
            r"[\s_-]+", " ", parenthetical.group(1).strip().casefold()
        )
        if label in _KNOWN_PARENTHETICAL_CUES:
            return candidate
    if all(character in _MUSICAL_CUE_CHARACTERS for character in candidate):
        return candidate
    return None


class WhisperTranscriber:
    def __init__(
        self,
        config: WhisperConfig,
        model_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.config = config
        verify_model(config)
        if model_factory is None:
            try:
                from pywhispercpp.model import Model
            except ImportError as error:
                raise TranscriptionError(
                    "pywhispercpp is unavailable; install Fedora package "
                    "python3-pywhispercpp"
                ) from error
            model_factory = Model

        parameters: dict[str, Any] = {
            "n_threads": config.threads,
            "print_progress": False,
            "print_realtime": False,
        }
        if config.language:
            parameters["language"] = config.language
        try:
            self._model = model_factory(str(config.model_path), **parameters)
        except Exception as error:
            raise TranscriptionError(
                f"Whisper model loading failed: {error}"
            ) from error

    def transcribe(self, audio_path: Path) -> Transcript:
        started = time.monotonic()
        try:
            segments = self._model.transcribe(str(audio_path))
        except Exception as error:
            raise TranscriptionError(f"Whisper transcription failed: {error}") from error
        text = "".join(str(segment.text) for segment in segments)
        return Transcript(
            text=text,
            elapsed_seconds=time.monotonic() - started,
            segment_count=len(segments),
        )


def prepare_model(config: WhisperConfig) -> Path:
    config.model_path.parent.mkdir(parents=True, exist_ok=True)
    if config.model_path.is_file():
        verify_model(config)
        return config.model_path
    try:
        from pywhispercpp.utils import download_model
    except ImportError as error:
        raise TranscriptionError(
            "pywhispercpp is unavailable; install Fedora package "
            "python3-pywhispercpp"
        ) from error

    try:
        downloaded_result = download_model(
            config.model_name, download_dir=str(config.model_path.parent)
        )
    except Exception as error:
        raise TranscriptionError(f"model download failed: {error}") from error
    if not downloaded_result:
        raise TranscriptionError(
            f"model download failed for configured model {config.model_name}"
        )
    downloaded = Path(downloaded_result)
    if not downloaded.is_file():
        raise TranscriptionError(
            f"model download reported success but no file exists at {downloaded}"
        )
    if downloaded.resolve() != config.model_path.resolve():
        if config.model_path.exists():
            raise TranscriptionError(
                f"refusing to replace existing model path {config.model_path}"
            )
        downloaded.rename(config.model_path)
    verify_model(config)
    return config.model_path


def verify_model(config: WhisperConfig) -> Path:
    path = config.model_path
    if not path.is_file():
        raise TranscriptionError(
            f"model is missing: {path}; run prepare-model explicitly"
        )
    try:
        size = path.stat().st_size
    except OSError as error:
        raise TranscriptionError(f"cannot inspect model {path}: {error}") from error
    if size != config.expected_size_bytes:
        raise TranscriptionError(
            f"model has unsafe size {size}, expected {config.expected_size_bytes}: {path}"
        )
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise TranscriptionError(f"cannot read model {path}: {error}") from error
    actual_sha256 = digest.hexdigest()
    if actual_sha256 != config.expected_sha256:
        raise TranscriptionError(
            f"model checksum mismatch: expected {config.expected_sha256}, "
            f"found {actual_sha256}: {path}"
        )
    return path
