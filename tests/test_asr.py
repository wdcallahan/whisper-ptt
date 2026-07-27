from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from nova_whisper_ptt.asr import (
    TranscriptionError,
    WhisperTranscriber,
    classify_transcript_annotation,
)
from nova_whisper_ptt.config import WhisperConfig


class AsrTests(unittest.TestCase):
    def test_classifies_annotation_only_transcripts(self) -> None:
        for text in (
            "[BLANK_AUDIO]",
            " [Music] ",
            "<|nospeech|>",
            "(silence)",
            "♪",
        ):
            with self.subTest(text=text):
                self.assertEqual(classify_transcript_annotation(text), text.strip())

    def test_does_not_classify_spoken_or_mixed_text_as_annotation(self) -> None:
        for text in (
            "blank audio",
            "I heard [music] next door.",
            "(this is ordinary dictated text)",
            "[1]",
        ):
            with self.subTest(text=text):
                self.assertIsNone(classify_transcript_annotation(text))

    def test_uses_existing_model_and_joins_segments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            model_path = Path(temporary) / "model.bin"
            model_path.write_bytes(b"safe-model")
            calls = []

            class Model:
                def __init__(self, path, **kwargs):
                    calls.append((path, kwargs))

                def transcribe(self, path):
                    return [
                        SimpleNamespace(text=" hello"),
                        SimpleNamespace(text=" world"),
                    ]

            transcriber = WhisperTranscriber(
                WhisperConfig(
                    model_path=model_path,
                    expected_size_bytes=model_path.stat().st_size,
                    expected_sha256=hashlib.sha256(b"safe-model").hexdigest(),
                ),
                model_factory=Model,
            )
            result = transcriber.transcribe(Path(temporary) / "audio.wav")
            self.assertEqual(result.text, " hello world")
            self.assertEqual(result.segment_count, 2)
            self.assertEqual(calls[0][0], str(model_path))
            self.assertEqual(calls[0][1]["n_threads"], 6)

    def test_rejects_model_with_wrong_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            model_path = Path(temporary) / "model.bin"
            model_path.write_bytes(b"unsafe-model")
            with self.assertRaisesRegex(
                TranscriptionError, "model checksum mismatch"
            ):
                WhisperTranscriber(
                    WhisperConfig(
                        model_path=model_path,
                        expected_size_bytes=model_path.stat().st_size,
                        expected_sha256="0" * 64,
                    ),
                    model_factory=lambda *_args, **_kwargs: None,
                )
