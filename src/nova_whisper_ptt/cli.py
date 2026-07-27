from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import select
import shutil
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .asr import TranscriptionError, WhisperTranscriber, prepare_model, verify_model
from .audio import PipeWireRecorder, RecorderError
from .config import Config, ConfigError, default_config_path, load_config
from .daemon import run_daemon
from .devices import (
    DeviceDiscoveryError,
    discover_device,
    inspect_candidates,
    key_code,
)
from .focus import FocusError, WindowCallsFocusGuard
from .inject import InjectionError, YdotoolInjector

LOG = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nova-whisper-ptt",
        description="Local release-to-finalize dictation for Nova's KEY_MACRO28.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=default_config_path(),
        help="TOML configuration path",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--verbose", action="store_true", help="enable debug journal/stderr logging"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser("doctor", help="perform read-only readiness checks")
    doctor.add_argument(
        "--allow-missing-model",
        action="store_true",
        help="do not fail while the explicit model-preparation step is pending",
    )
    doctor.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    commands.add_parser(
        "list-inputs", help="inspect only the configured Lemokey input candidates"
    )

    probe = commands.add_parser(
        "probe-key", help="observe one KEY_MACRO28 press/release without grabbing it"
    )
    probe.add_argument("--timeout", type=float, default=15.0)

    commands.add_parser(
        "prepare-model", help="explicitly download the configured local model"
    )

    record = commands.add_parser(
        "record-proof", help="record a timed WAV without transcribing or injecting"
    )
    record.add_argument("--seconds", type=float, default=5.0)
    record.add_argument("--output", type=Path)

    transcribe = commands.add_parser(
        "transcribe", help="transcribe an existing WAV without injecting it"
    )
    transcribe.add_argument("audio", type=Path)

    inject = commands.add_parser(
        "inject", help="explicitly inject reviewed ASCII text through ydotool"
    )
    inject.add_argument("text", nargs="?")

    commands.add_parser("daemon", help="run the push-to-talk state machine")
    commands.add_parser("state", help="print the current runtime state JSON")
    return parser


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": ok, "detail": detail}


def _pipewire_sources() -> tuple[set[str], str]:
    try:
        result = subprocess.run(
            ["/usr/bin/pw-dump"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return set(), str(error)
    if result.returncode != 0:
        return set(), result.stderr.strip()
    try:
        objects = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        return set(), f"pw-dump returned invalid JSON: {error}"
    if not isinstance(objects, list):
        return set(), "pw-dump did not return a JSON array"
    names: set[str] = set()
    for item in objects:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "PipeWire:Interface:Node":
            continue
        info = item.get("info", {})
        if not isinstance(info, dict):
            continue
        props = info.get("props", {})
        if not isinstance(props, dict):
            continue
        if props.get("media.class") == "Audio/Source" and props.get("node.name"):
            names.add(str(props["node.name"]))
    return names, ""


def doctor(config: Config, allow_missing_model: bool) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for name, path in (
        ("pw-record", config.audio.recorder),
        ("ydotool", config.injection.ydotool),
        ("dbus-send", config.focus.dbus_send),
    ):
        checks.append(
            _check(name, os.access(path, os.X_OK), f"executable path: {path}")
        )

    for module, package in (
        ("evdev", "python3-evdev"),
        ("pywhispercpp", "python3-pywhispercpp"),
    ):
        available = importlib.util.find_spec(module) is not None
        checks.append(
            _check(
                module,
                available,
                f"Python module {'available' if available else 'missing'} "
                f"(Fedora package {package})",
            )
        )

    model_exists = config.whisper.model_path.is_file()
    model_ok = False
    model_detail = f"pending explicit download: {config.whisper.model_path}"
    if model_exists:
        try:
            verify_model(config.whisper)
            model_ok = True
            model_detail = (
                "verified local model size and SHA-256: "
                f"{config.whisper.model_path}"
            )
        except TranscriptionError as error:
            model_detail = str(error)
    checks.append(
        _check(
            "model",
            model_ok or (allow_missing_model and not model_exists),
            model_detail,
        )
    )

    sources, source_error = _pipewire_sources()
    checks.append(
        _check(
            "microphone",
            config.audio.source in sources,
            (
                f"found PipeWire source {config.audio.source}"
                if config.audio.source in sources
                else source_error or f"source not found: {config.audio.source}"
            ),
        )
    )

    try:
        selected = discover_device(config.input)
        checks.append(
            _check(
                "whisper-key",
                True,
                f"{selected} uniquely advertises {config.input.key_name}",
            )
        )
    except DeviceDiscoveryError as error:
        checks.append(_check("whisper-key", False, str(error)))

    if config.focus.require_unchanged:
        try:
            focus = WindowCallsFocusGuard(config.focus).capture()
            checks.append(
                _check(
                    "focus-guard",
                    True,
                    f"focused window id={focus.window_id} class={focus.wm_class!r}",
                )
            )
        except FocusError as error:
            checks.append(_check("focus-guard", False, str(error)))

    try:
        result = subprocess.run(
            ["/usr/bin/systemctl", "--user", "is-active", "ydotool.service"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
        )
        active = result.returncode == 0 and result.stdout.strip() == "active"
        checks.append(
            _check(
                "ydotoold",
                active,
                f"ydotool.service is {result.stdout.strip() or 'unavailable'}",
            )
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        checks.append(_check("ydotoold", False, str(error)))
    return checks


def _print_doctor(checks: list[dict[str, Any]], as_json: bool) -> None:
    if as_json:
        print(json.dumps(checks, indent=2, sort_keys=True))
        return
    for check in checks:
        label = "PASS" if check["ok"] else "FAIL"
        print(f"{label}: {check['name']}: {check['detail']}")


def list_inputs(config: Config) -> int:
    candidates = inspect_candidates(config.input)
    if not candidates:
        print("No configured input paths exist.", file=sys.stderr)
        return 1
    for candidate in candidates:
        status = "MATCH" if candidate.readable and candidate.has_key else "skip"
        print(
            f"{status}: {candidate.path} -> {candidate.real_path}; "
            f"name={candidate.name!r}; phys={candidate.phys!r}; "
            f"{config.input.key_name}={candidate.has_key}; "
            f"error={candidate.error or 'none'}"
        )
    return 0 if sum(item.readable and item.has_key for item in candidates) == 1 else 1


def probe_key(config: Config, timeout: float) -> int:
    if timeout <= 0:
        raise ValueError("--timeout must be greater than zero")
    import evdev

    path = discover_device(config.input, evdev)
    expected = key_code(config.input.key_name, evdev)
    device = evdev.InputDevice(path)
    print(
        f"Listening without a grab on {path} ({device.name}); "
        f"press and release {config.input.key_name}."
    )
    deadline = time.monotonic() + timeout
    saw_press = False
    saw_release = False
    try:
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            ready, _, _ = select.select([device], [], [], min(1.0, remaining))
            if not ready:
                continue
            for event in device.read():
                if event.type != evdev.ecodes.EV_KEY or event.code != expected:
                    continue
                if event.value == 1:
                    saw_press = True
                    print("PRESS")
                elif event.value == 0:
                    saw_release = True
                    print("RELEASE")
                elif event.value == 2:
                    print("REPEAT (ignored by the daemon)")
                if saw_press and saw_release:
                    return 0
    finally:
        device.close()
    print("Timed out before observing one complete press/release.", file=sys.stderr)
    return 1


def record_proof(config: Config, seconds: float, output: Path | None) -> int:
    if seconds <= 0 or seconds > config.audio.maximum_duration_seconds:
        raise ValueError(
            f"--seconds must be between 0 and {config.audio.maximum_duration_seconds}"
        )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    utterance_dir = (
        config.runtime.state_dir
        / "proofs"
        / f"{stamp}-{uuid.uuid4().hex[:8]}"
    )
    if output is not None:
        output = output.expanduser()
        if output.exists():
            raise FileExistsError(f"refusing to overwrite existing output: {output}")
        if output.suffix.lower() != ".wav":
            raise ValueError("--output must name a .wav file")
    recorder = PipeWireRecorder(config.audio)
    recording = recorder.start(utterance_dir)
    print(f"Recording from the configured RØDE source for {seconds:g} seconds now.")
    try:
        time.sleep(seconds)
        audio_path = recorder.stop(recording)
    except BaseException:
        recorder.abort(recording)
        raise
    if output is not None and output.suffix:
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(audio_path), output)
        shutil.rmtree(utterance_dir)
        audio_path = output
    print(audio_path)
    return 0


def transcribe_file(config: Config, audio_path: Path) -> int:
    if not audio_path.is_file():
        raise FileNotFoundError(audio_path)
    result = WhisperTranscriber(config.whisper).transcribe(audio_path)
    print(result.text.strip())
    print(
        f"[{result.segment_count} segment(s), {result.elapsed_seconds:.3f}s]",
        file=sys.stderr,
    )
    return 0


def state(config: Config) -> int:
    path = config.runtime.runtime_dir / "state.json"
    if not path.is_file():
        print(f"No runtime state exists at {path}.", file=sys.stderr)
        return 1
    print(path.read_text(encoding="utf-8"), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = load_config(args.config)
        if args.command == "doctor":
            checks = doctor(config, args.allow_missing_model)
            _print_doctor(checks, args.json)
            return 0 if all(check["ok"] for check in checks) else 1
        if args.command == "list-inputs":
            return list_inputs(config)
        if args.command == "probe-key":
            return probe_key(config, args.timeout)
        if args.command == "prepare-model":
            print(prepare_model(config.whisper))
            return 0
        if args.command == "record-proof":
            return record_proof(config, args.seconds, args.output)
        if args.command == "transcribe":
            return transcribe_file(config, args.audio)
        if args.command == "inject":
            text = args.text if args.text is not None else sys.stdin.read()
            result = YdotoolInjector(config.injection).inject(text)
            print(f"Injected {result.character_count} characters.")
            return 0
        if args.command == "daemon":
            return run_daemon(config)
        if args.command == "state":
            return state(config)
    except (
        ConfigError,
        DeviceDiscoveryError,
        FocusError,
        InjectionError,
        RecorderError,
        TranscriptionError,
        FileExistsError,
        OSError,
        ValueError,
    ) as error:
        LOG.error("%s", error)
        return 1
    raise AssertionError(f"unhandled command: {args.command}")
