from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable

from .config import FeedbackConfig, RuntimeConfig

LOG = logging.getLogger(__name__)


class State(StrEnum):
    STARTING = "starting"
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    INJECTING = "injecting"
    ERROR = "error"
    STOPPED = "stopped"


@dataclass(frozen=True)
class StateSnapshot:
    state: State
    detail: str
    changed_at: float


class StatePublisher:
    def __init__(
        self,
        runtime: RuntimeConfig,
        feedback: FeedbackConfig,
        runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    ) -> None:
        self.runtime = runtime
        self.feedback = feedback
        self._runner = runner
        self._last: StateSnapshot | None = None
        self._notification_id: int | None = None
        self.runtime.runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    @property
    def state_path(self) -> Path:
        return self.runtime.runtime_dir / "state.json"

    def publish(self, state: State, detail: str = "") -> StateSnapshot:
        previous = self._last
        snapshot = StateSnapshot(state=state, detail=detail, changed_at=time.time())
        payload = {
            "state": snapshot.state.value,
            "detail": snapshot.detail,
            "changed_at": snapshot.changed_at,
            "pid": os.getpid(),
        }
        temporary = self.state_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8"
        )
        temporary.replace(self.state_path)
        self._last = snapshot
        LOG.info("state=%s detail=%s", state.value, detail)
        self._notify_state(snapshot, previous)
        return snapshot

    def notice(self, summary: str, body: str = "") -> None:
        self._notify(summary, body, "dialog-information-symbolic", 2500)

    def _notify_state(
        self, snapshot: StateSnapshot, previous: StateSnapshot | None
    ) -> None:
        if snapshot.state == State.RECORDING:
            self._notify(
                "Nova Whisper: recording",
                "Release Whisper to transcribe.",
                "audio-input-microphone-symbolic",
                0,
            )
        elif snapshot.state == State.TRANSCRIBING:
            self._notify(
                "Nova Whisper: transcribing",
                "The focused window must remain unchanged.",
                "system-run-symbolic",
                0,
            )
        elif snapshot.state == State.ERROR:
            self._notify(
                "Nova Whisper: attention required",
                snapshot.detail,
                "dialog-error-symbolic",
                8000,
            )
        elif (
            snapshot.state == State.IDLE
            and self.feedback.success_notification
            and previous is not None
            and previous.state in (State.TRANSCRIBING, State.INJECTING)
        ):
            self._notify(
                "Nova Whisper: ready",
                snapshot.detail,
                "emblem-ok-symbolic",
                1500,
            )

    def _notify(
        self, summary: str, body: str, icon: str, expire_ms: int
    ) -> None:
        if not self.feedback.notifications:
            return
        command = [
            self.feedback.notify_send,
            "--app-name=Nova Whisper",
            f"--icon={icon}",
            f"--expire-time={expire_ms}",
            "--print-id",
            summary,
        ]
        if self._notification_id is not None:
            command.insert(-1, f"--replace-id={self._notification_id}")
        if body:
            command.append(body)
        try:
            result = self._runner(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=2,
                check=False,
            )
            if result.returncode != 0:
                LOG.warning("notify-send exited with status %s", result.returncode)
            else:
                try:
                    self._notification_id = int(result.stdout.strip())
                except (TypeError, ValueError):
                    LOG.warning("notify-send did not return a notification id")
        except (OSError, subprocess.TimeoutExpired) as error:
            LOG.warning("desktop notification failed: %s", error)
