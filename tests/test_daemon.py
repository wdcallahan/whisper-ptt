from __future__ import annotations

import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from nova_whisper_ptt.asr import Transcript
from nova_whisper_ptt.config import (
    AudioConfig,
    Config,
    FeedbackConfig,
    FocusConfig,
    InjectionConfig,
    InputConfig,
    RuntimeConfig,
    WhisperConfig,
)
from nova_whisper_ptt.daemon import PushToTalkController
from nova_whisper_ptt.focus import FocusError, FocusToken
from nova_whisper_ptt.inject import InjectionResult
from nova_whisper_ptt.state import State


class FakePublisher:
    def __init__(self) -> None:
        self.states = []
        self.notices = []

    def publish(self, state, detail=""):
        self.states.append((state, detail))

    def notice(self, summary, body=""):
        self.notices.append((summary, body))


class FakeRecorder:
    def start(self, utterance_dir):
        utterance_dir.mkdir(parents=True, exist_ok=False)
        return SimpleNamespace(
            audio_path=utterance_dir / "audio.wav",
            duration_ms=500,
        )

    def stop(self, recording):
        recording.audio_path.write_bytes(b"R" * 100)
        return recording.audio_path

    def abort(self, recording):
        pass


class FakeTranscriber:
    def transcribe(self, audio_path):
        return Transcript(" hello world", 0.01, 1)


class FakeInjector:
    def __init__(self) -> None:
        self.texts = []

    def inject(self, text):
        self.texts.append(text)
        return InjectionResult(len(text))


class FakeFocus:
    def __init__(self, changed_checks=()) -> None:
        self.changed_checks = set(changed_checks)
        self.check_count = 0

    def capture(self):
        return FocusToken(42, "org.gnome.Ptyxis")

    def require_same(self, original):
        self.check_count += 1
        if self.check_count in self.changed_checks:
            raise FocusError("focused window changed during proof")


class FakeTimer:
    def __init__(self, interval, function) -> None:
        self.interval = interval
        self.function = function
        self.daemon = False

    def start(self):
        pass

    def cancel(self):
        pass


def make_config(root: Path) -> Config:
    model = root / "model.bin"
    model.touch()
    return Config(
        input=InputConfig(device="/dev/input/fake"),
        audio=AudioConfig(),
        whisper=WhisperConfig(model_path=model),
        injection=InjectionConfig(),
        focus=FocusConfig(),
        feedback=FeedbackConfig(notifications=False),
        runtime=RuntimeConfig(
            state_dir=root / "state",
            runtime_dir=root / "runtime",
            retain_successful_audio=False,
        ),
    )


class ControllerTests(unittest.TestCase):
    def _wait_for(self, controller, state):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if controller.state == state:
                return
            time.sleep(0.01)
        self.fail(f"controller did not reach {state}; current={controller.state}")

    def test_press_release_injects_once_and_ignores_second_press(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            publisher = FakePublisher()
            injector = FakeInjector()
            controller = PushToTalkController(
                make_config(root),
                FakeRecorder(),
                FakeTranscriber(),
                injector,
                FakeFocus(),
                publisher,
                timer_factory=FakeTimer,
            )
            controller.ready()
            self.assertTrue(controller.press())
            with self.assertLogs("nova_whisper_ptt.daemon", level="WARNING"):
                self.assertFalse(controller.press())
            self.assertTrue(controller.release())
            self._wait_for(controller, State.IDLE)
            self.assertEqual(injector.texts, ["hello world "])
            self.assertEqual(
                [state for state, _ in publisher.states],
                [
                    State.IDLE,
                    State.RECORDING,
                    State.TRANSCRIBING,
                    State.INJECTING,
                    State.IDLE,
                ],
            )

    def test_focus_change_preserves_audio_and_transcript_without_injection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = replace(
                make_config(root),
                runtime=RuntimeConfig(
                    state_dir=root / "state",
                    runtime_dir=root / "runtime",
                    retain_successful_audio=False,
                ),
            )
            publisher = FakePublisher()
            injector = FakeInjector()
            controller = PushToTalkController(
                config,
                FakeRecorder(),
                FakeTranscriber(),
                injector,
                FakeFocus(changed_checks=(1,)),
                publisher,
                timer_factory=FakeTimer,
            )
            controller.ready()
            controller.press()
            with self.assertLogs("nova_whisper_ptt.daemon", level="ERROR"):
                controller.release()
                self._wait_for(controller, State.ERROR)
            self.assertEqual(injector.texts, [])
            utterances = list((root / "state" / "utterances").iterdir())
            self.assertEqual(len(utterances), 1)
            self.assertTrue((utterances[0] / "audio.wav").is_file())
            self.assertTrue((utterances[0] / "transcript.raw.txt").is_file())
            self.assertTrue((utterances[0] / "transcript.txt").is_file())
            self.assertTrue((utterances[0] / "failure.txt").is_file())

    def test_focus_change_after_injection_warns_and_preserves_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            publisher = FakePublisher()
            injector = FakeInjector()
            controller = PushToTalkController(
                make_config(root),
                FakeRecorder(),
                FakeTranscriber(),
                injector,
                FakeFocus(changed_checks=(2,)),
                publisher,
                timer_factory=FakeTimer,
            )
            controller.ready()
            controller.press()
            with self.assertLogs("nova_whisper_ptt.daemon", level="ERROR"):
                controller.release()
                self._wait_for(controller, State.ERROR)

            self.assertEqual(injector.texts, ["hello world "])
            utterances = list((root / "state" / "utterances").iterdir())
            self.assertEqual(len(utterances), 1)
            failure = (utterances[0] / "failure.txt").read_text(encoding="utf-8")
            self.assertIn("already emitted", failure)
            self.assertIn("may have been split across windows", failure)
            metrics = (root / "state" / "metrics.jsonl").read_text(
                encoding="utf-8"
            )
            self.assertIn('"outcome": "focus-changed-after-injection"', metrics)
            self.assertIn('"character_count": 12', metrics)
