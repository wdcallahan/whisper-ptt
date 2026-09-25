from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from nova_whisper_ptt.asr import (
    TranscriptionError,
    WhisperTranscriber,
    canonicalize_transcript_text,
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

    def test_canonicalizes_recurring_linux_terms_without_guessing_bare_iperf(self) -> None:
        text = (
            "I used su2, sud, SU-DU, and SU due with the system d service, SystemCTL, "
            "JournalCTL, SE Linux, Firewall D, Pipewire, pod man, a quadlet, "
            "ansible, and I-Perf III."
        )
        self.assertEqual(
            canonicalize_transcript_text(text),
            (
                "I used sudo, sudo, sudo, and sudo with the systemd service, systemctl, "
                "journalctl, SELinux, firewalld, PipeWire, Podman, a Quadlet, "
                "Ansible, and iperf3."
            ),
        )
        self.assertEqual(
            canonicalize_transcript_text("I used iperf between two hosts."),
            "I used iperf between two hosts.",
        )

    def test_uses_existing_model_joins_segments_and_passes_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            model_path = Path(temporary) / "model.bin"
            model_path.write_bytes(b"safe-model")
            calls = []
            transcribe_calls = []

            class Model:
                def __init__(self, path, **kwargs):
                    calls.append((path, kwargs))

                def transcribe(self, path, **kwargs):
                    transcribe_calls.append((path, kwargs))
                    return [
                        SimpleNamespace(text="Sentence one."),
                        SimpleNamespace(text="Sentence two."),
                    ]

            transcriber = WhisperTranscriber(
                WhisperConfig(
                    model_path=model_path,
                    expected_size_bytes=model_path.stat().st_size,
                    expected_sha256=hashlib.sha256(b"safe-model").hexdigest(),
                    initial_prompt="Joule, Pixel, Arcane Sanctum.",
                ),
                model_factory=Model,
            )
            result = transcriber.transcribe(Path(temporary) / "audio.wav")
            self.assertEqual(result.text, "Sentence one. Sentence two.")
            self.assertEqual(result.segment_count, 2)
            self.assertEqual(calls[0][0], str(model_path))
            self.assertEqual(calls[0][1]["n_threads"], 6)
            self.assertEqual(
                transcribe_calls,
                [
                    (
                        str(Path(temporary) / "audio.wav"),
                        {"initial_prompt": "Joule, Pixel, Arcane Sanctum."},
                    )
                ],
            )

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
