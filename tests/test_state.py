from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from nova_whisper_ptt.config import FeedbackConfig, RuntimeConfig
from nova_whisper_ptt.state import State, StatePublisher


class StateTests(unittest.TestCase):
    def test_state_is_atomic_and_notifications_replace_one_another(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            calls = []
            notification_ids = iter((b"41\n", b"41\n", b"41\n"))

            def runner(command, **kwargs):
                calls.append((command, kwargs))
                return subprocess.CompletedProcess(
                    command, 0, next(notification_ids), b""
                )

            publisher = StatePublisher(
                RuntimeConfig(
                    state_dir=root / "state",
                    runtime_dir=root / "runtime",
                ),
                FeedbackConfig(),
                runner=runner,
            )
            publisher.publish(State.RECORDING, "first")
            publisher.publish(State.TRANSCRIBING, "second")
            publisher.publish(State.IDLE, "third")

            payload = json.loads(publisher.state_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["state"], "idle")
            self.assertEqual(payload["detail"], "third")
            self.assertNotIn("--replace-id=41", calls[0][0])
            self.assertIn("--replace-id=41", calls[1][0])
            self.assertIn("--replace-id=41", calls[2][0])
            self.assertFalse(
                publisher.state_path.with_suffix(".json.tmp").exists()
            )

    def test_error_notification_requests_attention_without_claiming_safety(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            calls = []

            def runner(command, **kwargs):
                calls.append((command, kwargs))
                return subprocess.CompletedProcess(command, 0, b"42\n", b"")

            publisher = StatePublisher(
                RuntimeConfig(
                    state_dir=root / "state",
                    runtime_dir=root / "runtime",
                ),
                FeedbackConfig(),
                runner=runner,
            )
            publisher.publish(State.ERROR, "Text may span two windows.")

            self.assertIn("Nova Whisper: attention required", calls[0][0])
            self.assertNotIn("Nova Whisper stopped safely", calls[0][0])
