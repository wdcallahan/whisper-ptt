from __future__ import annotations

import json
import logging
import select
import shutil
import signal
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .asr import WhisperTranscriber
from .audio import PipeWireRecorder, Recording
from .config import Config
from .devices import DeviceDiscoveryError, discover_device, key_code
from .focus import FocusError, FocusToken, WindowCallsFocusGuard
from .inject import InjectionError, YdotoolInjector, normalize_text
from .state import State, StatePublisher

LOG = logging.getLogger(__name__)


class PushToTalkController:
    def __init__(
        self,
        config: Config,
        recorder: PipeWireRecorder,
        transcriber: WhisperTranscriber,
        injector: YdotoolInjector,
        focus_guard: WindowCallsFocusGuard,
        publisher: StatePublisher,
        worker_factory: Callable[..., threading.Thread] = threading.Thread,
        timer_factory: Callable[..., threading.Timer] = threading.Timer,
    ) -> None:
        self.config = config
        self.recorder = recorder
        self.transcriber = transcriber
        self.injector = injector
        self.focus_guard = focus_guard
        self.publisher = publisher
        self._worker_factory = worker_factory
        self._timer_factory = timer_factory
        self._lock = threading.RLock()
        self._state = State.STARTING
        self._recording: Recording | None = None
        self._utterance_dir: Path | None = None
        self._focus: FocusToken | None = None
        self._maximum_timer: threading.Timer | None = None
        self._workers: set[threading.Thread] = set()
        self._shutting_down = False

    @property
    def state(self) -> State:
        with self._lock:
            return self._state

    def ready(self) -> None:
        with self._lock:
            self._set_state(State.IDLE, "Ready for KEY_MACRO28.")

    def press(self) -> bool:
        with self._lock:
            if self._shutting_down:
                return False
            if self._state == State.ERROR:
                self._set_state(State.IDLE, "Cleared the previous error.")
            if self._state != State.IDLE:
                self.publisher.notice(
                    "Nova Whisper is busy",
                    f"Ignored a new press while {self._state.value}.",
                )
                LOG.warning("ignored press while state=%s", self._state.value)
                return False

            utterance_dir: Path | None = None
            try:
                focus = (
                    self.focus_guard.capture()
                    if self.config.focus.require_unchanged
                    else None
                )
                utterance_dir = self._new_utterance_dir()
                recording = self.recorder.start(utterance_dir)
            except Exception as error:
                self._record_failure(
                    utterance_dir, error, f"recording did not start: {error}"
                )
                return False

            self._focus = focus
            self._utterance_dir = utterance_dir
            self._recording = recording
            self._set_state(State.RECORDING, f"Recording to {recording.audio_path}.")
            timer = self._timer_factory(
                self.config.audio.maximum_duration_seconds,
                self._maximum_duration_reached,
            )
            timer.daemon = True
            timer.start()
            self._maximum_timer = timer
            return True

    def release(self) -> bool:
        with self._lock:
            if self._state != State.RECORDING or self._recording is None:
                LOG.info("ignored unmatched release while state=%s", self._state.value)
                return False
            recording = self._recording
            utterance_dir = self._utterance_dir
            focus = self._focus
            self._recording = None
            self._utterance_dir = None
            self._focus = None
            self._cancel_maximum_timer()
            self._set_state(State.TRANSCRIBING, "Finalizing the captured utterance.")
            self._start_worker(self._finish_utterance, recording, utterance_dir, focus)
            return True

    def device_lost(self, reason: str) -> None:
        with self._lock:
            if self._state == State.RECORDING and self._recording is not None:
                recording = self._recording
                utterance_dir = self._utterance_dir
                self._recording = None
                self._utterance_dir = None
                self._focus = None
                self._cancel_maximum_timer()
                self._record_failure(
                    utterance_dir, None, f"input device disappeared: {reason}"
                )
                self._start_worker(self.recorder.abort, recording)
            elif self._state in (State.IDLE, State.ERROR):
                self._set_state(State.ERROR, reason)

    def device_restored(self, path: str) -> None:
        with self._lock:
            if self._state == State.ERROR and not self._shutting_down:
                self._set_state(State.IDLE, f"Listening on {path}.")

    def shutdown(self) -> None:
        with self._lock:
            self._shutting_down = True
            self._cancel_maximum_timer()
            recording = self._recording
            utterance_dir = self._utterance_dir
            self._recording = None
            self._utterance_dir = None
            self._focus = None
            if recording is not None:
                self._record_failure(
                    utterance_dir, None, "service stopped during recording"
                )
                self.recorder.abort(recording)
            workers = list(self._workers)
        for worker in workers:
            worker.join(timeout=5)
        with self._lock:
            self._set_state(State.STOPPED, "Service stopped.")

    def _finish_utterance(
        self,
        recording: Recording,
        utterance_dir: Path | None,
        focus: FocusToken | None,
    ) -> None:
        assert utterance_dir is not None
        released_at = time.monotonic()
        try:
            audio_path = self.recorder.stop(recording)
            recording_ms = recording.duration_ms
            if recording_ms < self.config.audio.minimum_duration_ms:
                self._record_metric(
                    "too-short",
                    recording_ms,
                    time.monotonic() - released_at,
                    0,
                )
                self._complete_success(
                    utterance_dir, "Utterance was too short; inserted nothing."
                )
                return

            transcript = self.transcriber.transcribe(audio_path)
            (utterance_dir / "transcript.raw.txt").write_text(
                transcript.text + "\n", encoding="utf-8"
            )
            normalized = normalize_text(transcript.text, self.config.injection)
            if not normalized:
                self._record_metric(
                    "empty",
                    recording_ms,
                    time.monotonic() - released_at,
                    0,
                )
                self._complete_success(
                    utterance_dir, "No speech recognized; inserted nothing."
                )
                return

            (utterance_dir / "transcript.txt").write_text(
                normalized + "\n", encoding="utf-8"
            )
            with self._lock:
                if self._shutting_down:
                    raise InjectionError("service stopped before text injection")
                self._set_state(State.INJECTING, "Checking focus and inserting text.")
            if self.config.focus.require_unchanged:
                if focus is None:
                    raise FocusError("no original focused-window token was captured")
                self.focus_guard.require_same(focus)
            result = self.injector.inject(normalized)
            self._record_metric(
                "inserted",
                recording_ms,
                time.monotonic() - released_at,
                result.character_count,
            )
            self._complete_success(
                utterance_dir,
                f"Inserted {result.character_count} characters in "
                f"{time.monotonic() - released_at:.2f}s.",
            )
        except Exception as error:
            self._record_failure(utterance_dir, error, str(error))

    def _maximum_duration_reached(self) -> None:
        with self._lock:
            if self._state != State.RECORDING or self._recording is None:
                return
            recording = self._recording
            utterance_dir = self._utterance_dir
            self._recording = None
            self._utterance_dir = None
            self._focus = None
            self._maximum_timer = None
            self._record_failure(
                utterance_dir,
                None,
                "maximum recording duration exceeded; recording was stopped",
            )
            self._start_worker(self.recorder.abort, recording)

    def _start_worker(self, target: Callable[..., Any], *args: Any) -> None:
        worker = self._worker_factory(
            target=self._run_worker, args=(target, args), daemon=True
        )
        self._workers.add(worker)
        worker.start()

    def _run_worker(self, target: Callable[..., Any], args: tuple[Any, ...]) -> None:
        try:
            target(*args)
        finally:
            current = threading.current_thread()
            with self._lock:
                self._workers.discard(current)

    def _cancel_maximum_timer(self) -> None:
        if self._maximum_timer is not None:
            self._maximum_timer.cancel()
            self._maximum_timer = None

    def _new_utterance_dir(self) -> Path:
        root = self.config.runtime.state_dir / "utterances"
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        return root / f"{stamp}-{uuid.uuid4().hex[:8]}"

    def _complete_success(self, utterance_dir: Path, detail: str) -> None:
        if not self.config.runtime.retain_successful_audio:
            shutil.rmtree(utterance_dir)
        with self._lock:
            if not self._shutting_down:
                self._set_state(State.IDLE, detail)

    def _record_failure(
        self,
        utterance_dir: Path | None,
        error: Exception | None,
        detail: str,
    ) -> None:
        if error is not None:
            LOG.error(
                detail,
                exc_info=(type(error), error, error.__traceback__),
            )
        else:
            LOG.error(detail)
        if utterance_dir is not None:
            utterance_dir.mkdir(parents=True, exist_ok=True)
            (utterance_dir / "failure.txt").write_text(
                detail + "\n", encoding="utf-8"
            )
        with self._lock:
            if not self._shutting_down:
                self._set_state(State.ERROR, detail)

    def _record_metric(
        self,
        outcome: str,
        recording_ms: int,
        release_to_completion_seconds: float,
        character_count: int,
    ) -> None:
        self.config.runtime.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "outcome": outcome,
            "recording_ms": recording_ms,
            "release_to_completion_seconds": round(
                release_to_completion_seconds, 6
            ),
            "character_count": character_count,
        }
        with (self.config.runtime.state_dir / "metrics.jsonl").open(
            "a", encoding="utf-8"
        ) as stream:
            stream.write(json.dumps(payload, sort_keys=True) + "\n")

    def _set_state(self, state: State, detail: str) -> None:
        self._state = state
        self.publisher.publish(state, detail)


class EvdevListener:
    def __init__(
        self,
        config: Config,
        controller: PushToTalkController,
        stop_event: threading.Event,
        evdev_module: Any | None = None,
    ) -> None:
        self.config = config
        self.controller = controller
        self.stop_event = stop_event
        if evdev_module is None:
            import evdev

            evdev_module = evdev
        self.evdev = evdev_module
        self.expected_code = key_code(config.input.key_name, self.evdev)

    def run(self) -> None:
        last_error = ""
        while not self.stop_event.is_set():
            device = None
            try:
                path = discover_device(self.config.input, self.evdev)
                device = self.evdev.InputDevice(path)
                LOG.info(
                    "listening path=%s name=%s phys=%s",
                    path,
                    device.name,
                    device.phys,
                )
                self.controller.device_restored(path)
                last_error = ""
                while not self.stop_event.is_set():
                    ready, _, _ = select.select([device], [], [], 1.0)
                    if not ready:
                        continue
                    for event in device.read():
                        if (
                            event.type != self.evdev.ecodes.EV_KEY
                            or event.code != self.expected_code
                        ):
                            continue
                        if event.value == 1:
                            self.controller.press()
                        elif event.value == 0:
                            self.controller.release()
                        elif event.value == 2:
                            LOG.debug("ignored KEY_MACRO28 repeat")
            except (DeviceDiscoveryError, FileNotFoundError, PermissionError, OSError) as error:
                message = f"Whisper input unavailable: {error}"
                if message != last_error or self.controller.state == State.IDLE:
                    LOG.error(message)
                    self.controller.device_lost(message)
                    last_error = message
                self.stop_event.wait(self.config.input.reconnect_seconds)
            finally:
                if device is not None:
                    device.close()


def run_daemon(config: Config) -> int:
    publisher = StatePublisher(config.runtime, config.feedback)
    publisher.publish(State.STARTING, "Loading the local Whisper model.")
    transcriber = WhisperTranscriber(config.whisper)
    controller = PushToTalkController(
        config=config,
        recorder=PipeWireRecorder(config.audio),
        transcriber=transcriber,
        injector=YdotoolInjector(config.injection),
        focus_guard=WindowCallsFocusGuard(config.focus),
        publisher=publisher,
    )
    controller.ready()
    stop_event = threading.Event()

    def stop(_signum: int, _frame: Any) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    listener = EvdevListener(config, controller, stop_event)
    try:
        listener.run()
    finally:
        controller.shutdown()
    return 0
