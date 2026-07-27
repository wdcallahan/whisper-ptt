from __future__ import annotations

import json
import subprocess
import unittest

from nova_whisper_ptt.config import FocusConfig
from nova_whisper_ptt.focus import FocusError, WindowCallsFocusGuard


class SequenceRunner:
    def __init__(self, payloads):
        self.payloads = iter(payloads)

    def __call__(self, command, **kwargs):
        payload = next(self.payloads)
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")


class FocusTests(unittest.TestCase):
    def test_accepts_unchanged_focused_window(self) -> None:
        windows = [{"id": 42, "wm_class": "org.gnome.Ptyxis", "focus": True}]
        guard = WindowCallsFocusGuard(
            FocusConfig(), runner=SequenceRunner([windows, windows])
        )
        token = guard.capture()
        guard.require_same(token)
        self.assertEqual(token.window_id, 42)

    def test_rejects_changed_focused_window(self) -> None:
        first = [{"id": 42, "wm_class": "org.gnome.Ptyxis", "focus": True}]
        second = [{"id": 99, "wm_class": "firefox", "focus": True}]
        guard = WindowCallsFocusGuard(
            FocusConfig(), runner=SequenceRunner([first, second])
        )
        token = guard.capture()
        with self.assertRaisesRegex(FocusError, "focused window changed"):
            guard.require_same(token)
