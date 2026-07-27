from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when the configuration is absent or unsafe."""


def _xdg_path(variable: str, fallback: str) -> Path:
    return Path(os.environ.get(variable, fallback)).expanduser()


def default_config_path() -> Path:
    return _xdg_path("XDG_CONFIG_HOME", "~/.config") / "nova-whisper-ptt" / "config.toml"


def default_state_dir() -> Path:
    return _xdg_path("XDG_STATE_HOME", "~/.local/state") / "nova-whisper-ptt"


def default_runtime_dir() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "nova-whisper-ptt"
    return Path(f"/run/user/{os.getuid()}") / "nova-whisper-ptt"


def default_model_path() -> Path:
    return (
        _xdg_path("XDG_DATA_HOME", "~/.local/share")
        / "nova-whisper-ptt"
        / "models"
        / "ggml-base.en.bin"
    )


def _path(value: str | Path) -> Path:
    return Path(os.path.expandvars(str(value))).expanduser()


def _table(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a TOML table")
    return value


def _boolean(data: dict[str, Any], name: str, default: bool) -> bool:
    value = data.get(name, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{name} must be a TOML boolean")
    return value


@dataclass(frozen=True)
class InputConfig:
    key_name: str = "KEY_MACRO28"
    device: str = ""
    device_globs: tuple[str, ...] = (
        "/dev/input/by-id/usb-Keychron_Lemokey_X2-event-if01",
    )
    reconnect_seconds: float = 1.0


@dataclass(frozen=True)
class AudioConfig:
    source: str = (
        "alsa_input.usb-R__DE_Microphones_"
        "R__DE_NT-USB_Mini_14B577D5-00.mono-fallback"
    )
    recorder: str = "/usr/bin/pw-record"
    rate: int = 16_000
    channels: int = 1
    sample_format: str = "s16"
    minimum_duration_ms: int = 250
    maximum_duration_seconds: float = 180.0
    stop_timeout_seconds: float = 3.0


@dataclass(frozen=True)
class WhisperConfig:
    model_name: str = "base.en"
    model_path: Path = default_model_path()
    expected_size_bytes: int = 147_964_211
    expected_sha256: str = (
        "a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002"
    )
    threads: int = 6
    language: str = "en"


@dataclass(frozen=True)
class InjectionConfig:
    enabled: bool = True
    ydotool: str = "/usr/bin/ydotool"
    ascii_only: bool = True
    trailing_space: bool = False
    timeout_seconds: float = 15.0


@dataclass(frozen=True)
class FocusConfig:
    require_unchanged: bool = True
    dbus_send: str = "/usr/bin/dbus-send"
    timeout_seconds: float = 3.0


@dataclass(frozen=True)
class FeedbackConfig:
    notifications: bool = True
    notify_send: str = "/usr/bin/notify-send"
    success_notification: bool = True


@dataclass(frozen=True)
class RuntimeConfig:
    state_dir: Path = default_state_dir()
    runtime_dir: Path = default_runtime_dir()
    retain_successful_audio: bool = False


@dataclass(frozen=True)
class Config:
    input: InputConfig
    audio: AudioConfig
    whisper: WhisperConfig
    injection: InjectionConfig
    focus: FocusConfig
    feedback: FeedbackConfig
    runtime: RuntimeConfig


def load_config(path: Path | str | None = None) -> Config:
    config_path = _path(path) if path else default_config_path()
    try:
        with config_path.open("rb") as stream:
            data = tomllib.load(stream)
    except FileNotFoundError as error:
        raise ConfigError(f"configuration does not exist: {config_path}") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"invalid TOML in {config_path}: {error}") from error

    input_data = _table(data, "input")
    audio_data = _table(data, "audio")
    whisper_data = _table(data, "whisper")
    injection_data = _table(data, "injection")
    focus_data = _table(data, "focus")
    feedback_data = _table(data, "feedback")
    runtime_data = _table(data, "runtime")

    raw_globs = input_data.get("device_globs", InputConfig.device_globs)
    if (
        not isinstance(raw_globs, list | tuple)
        or not raw_globs
        or not all(isinstance(item, str) and item for item in raw_globs)
    ):
        raise ConfigError("input.device_globs must be a nonempty array of strings")

    input_config = InputConfig(
        key_name=str(input_data.get("key_name", "KEY_MACRO28")),
        device=str(input_data.get("device", "")),
        device_globs=tuple(raw_globs),
        reconnect_seconds=float(input_data.get("reconnect_seconds", 1.0)),
    )
    audio_config = AudioConfig(
        source=str(audio_data.get("source", AudioConfig.source)),
        recorder=str(audio_data.get("recorder", "/usr/bin/pw-record")),
        rate=int(audio_data.get("rate", 16_000)),
        channels=int(audio_data.get("channels", 1)),
        sample_format=str(audio_data.get("sample_format", "s16")),
        minimum_duration_ms=int(audio_data.get("minimum_duration_ms", 250)),
        maximum_duration_seconds=float(
            audio_data.get("maximum_duration_seconds", 180.0)
        ),
        stop_timeout_seconds=float(audio_data.get("stop_timeout_seconds", 3.0)),
    )
    whisper_config = WhisperConfig(
        model_name=str(whisper_data.get("model_name", "base.en")),
        model_path=_path(whisper_data.get("model_path", default_model_path())),
        expected_size_bytes=int(
            whisper_data.get("expected_size_bytes", 147_964_211)
        ),
        expected_sha256=str(
            whisper_data.get(
                "expected_sha256",
                "a03779c86df3323075f5e796cb2ce502"
                "9f00ec8869eee3fdfb897afe36c6d002",
            )
        ),
        threads=int(whisper_data.get("threads", 6)),
        language=str(whisper_data.get("language", "en")),
    )
    injection_config = InjectionConfig(
        enabled=_boolean(injection_data, "enabled", True),
        ydotool=str(injection_data.get("ydotool", "/usr/bin/ydotool")),
        ascii_only=_boolean(injection_data, "ascii_only", True),
        trailing_space=_boolean(injection_data, "trailing_space", False),
        timeout_seconds=float(injection_data.get("timeout_seconds", 15.0)),
    )
    focus_config = FocusConfig(
        require_unchanged=_boolean(focus_data, "require_unchanged", True),
        dbus_send=str(focus_data.get("dbus_send", "/usr/bin/dbus-send")),
        timeout_seconds=float(focus_data.get("timeout_seconds", 3.0)),
    )
    feedback_config = FeedbackConfig(
        notifications=_boolean(feedback_data, "notifications", True),
        notify_send=str(feedback_data.get("notify_send", "/usr/bin/notify-send")),
        success_notification=_boolean(
            feedback_data, "success_notification", True
        ),
    )
    runtime_config = RuntimeConfig(
        state_dir=_path(runtime_data.get("state_dir", default_state_dir())),
        runtime_dir=_path(runtime_data.get("runtime_dir", default_runtime_dir())),
        retain_successful_audio=_boolean(
            runtime_data, "retain_successful_audio", False
        ),
    )

    config = Config(
        input=input_config,
        audio=audio_config,
        whisper=whisper_config,
        injection=injection_config,
        focus=focus_config,
        feedback=feedback_config,
        runtime=runtime_config,
    )
    validate_config(config)
    return config


def validate_config(config: Config) -> None:
    if config.input.key_name != "KEY_MACRO28":
        raise ConfigError(
            "the 0.1 service boundary only accepts input.key_name=KEY_MACRO28"
        )
    if not config.input.device and not config.input.device_globs:
        raise ConfigError("configure input.device or at least one input.device_globs entry")
    if config.input.reconnect_seconds <= 0:
        raise ConfigError("input.reconnect_seconds must be greater than zero")
    if not config.audio.source:
        raise ConfigError("audio.source must be an explicit PipeWire node.name")
    if config.audio.rate != 16_000:
        raise ConfigError("the first accepted audio path requires audio.rate=16000")
    if config.audio.channels != 1:
        raise ConfigError("the first accepted audio path requires audio.channels=1")
    if config.audio.sample_format != "s16":
        raise ConfigError("the first accepted audio path requires audio.sample_format=s16")
    if config.audio.minimum_duration_ms < 0:
        raise ConfigError("audio.minimum_duration_ms cannot be negative")
    if config.audio.maximum_duration_seconds <= 0:
        raise ConfigError("audio.maximum_duration_seconds must be greater than zero")
    if config.audio.stop_timeout_seconds <= 0:
        raise ConfigError("audio.stop_timeout_seconds must be greater than zero")
    if config.whisper.threads < 1:
        raise ConfigError("whisper.threads must be at least one")
    if config.whisper.model_name != "base.en":
        raise ConfigError(
            "the 0.1 verified model boundary requires whisper.model_name=base.en"
        )
    if config.whisper.expected_size_bytes <= 0:
        raise ConfigError("whisper.expected_size_bytes must be greater than zero")
    if (
        len(config.whisper.expected_sha256) != 64
        or any(
            character not in "0123456789abcdef"
            for character in config.whisper.expected_sha256
        )
    ):
        raise ConfigError(
            "whisper.expected_sha256 must be 64 lowercase hexadecimal characters"
        )
    if config.injection.timeout_seconds <= 0:
        raise ConfigError("injection.timeout_seconds must be greater than zero")
    if config.focus.timeout_seconds <= 0:
        raise ConfigError("focus.timeout_seconds must be greater than zero")
