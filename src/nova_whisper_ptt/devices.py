from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import InputConfig


class DeviceDiscoveryError(RuntimeError):
    """Raised when the configured physical transport cannot be identified."""


@dataclass(frozen=True)
class DeviceCandidate:
    path: str
    real_path: str
    name: str
    phys: str
    has_key: bool
    readable: bool
    error: str = ""


def key_code(key_name: str, evdev_module: Any | None = None) -> int:
    evdev = evdev_module or _import_evdev()
    try:
        value = getattr(evdev.ecodes, key_name)
    except AttributeError as error:
        raise DeviceDiscoveryError(
            f"python-evdev does not define {key_name}"
        ) from error
    if not isinstance(value, int):
        raise DeviceDiscoveryError(f"{key_name} did not resolve to one key code")
    return value


def candidate_paths(config: InputConfig) -> list[str]:
    if config.device:
        return [config.device]

    paths: list[str] = []
    for pattern in config.device_globs:
        matches = glob.glob(pattern)
        if matches:
            paths.extend(sorted(matches))
        elif not glob.has_magic(pattern):
            paths.append(pattern)

    result: list[str] = []
    seen: set[str] = set()
    for path in paths:
        identity = os.path.realpath(path)
        if identity not in seen:
            seen.add(identity)
            result.append(path)
    return result


def inspect_candidates(
    config: InputConfig, evdev_module: Any | None = None
) -> list[DeviceCandidate]:
    evdev = evdev_module or _import_evdev()
    expected_code = key_code(config.key_name, evdev)
    candidates: list[DeviceCandidate] = []

    for path in candidate_paths(config):
        device = None
        try:
            device = evdev.InputDevice(path)
            capabilities = device.capabilities()
            keys = capabilities.get(evdev.ecodes.EV_KEY, [])
            candidates.append(
                DeviceCandidate(
                    path=path,
                    real_path=os.path.realpath(path),
                    name=str(device.name or ""),
                    phys=str(device.phys or ""),
                    has_key=expected_code in keys,
                    readable=True,
                )
            )
        except (FileNotFoundError, PermissionError, OSError) as error:
            candidates.append(
                DeviceCandidate(
                    path=path,
                    real_path=os.path.realpath(path),
                    name="",
                    phys="",
                    has_key=False,
                    readable=False,
                    error=f"{type(error).__name__}: {error}",
                )
            )
        finally:
            if device is not None:
                device.close()

    return candidates


def discover_device(config: InputConfig, evdev_module: Any | None = None) -> str:
    candidates = inspect_candidates(config, evdev_module)
    matches = [
        candidate
        for candidate in candidates
        if candidate.readable and candidate.has_key
    ]
    if len(matches) == 1:
        return matches[0].path
    if not matches:
        checked = ", ".join(candidate.path for candidate in candidates)
        raise DeviceDiscoveryError(
            f"no readable configured input device advertises {config.key_name}; "
            f"checked: {checked or 'no paths'}"
        )
    raise DeviceDiscoveryError(
        f"more than one configured input device advertises {config.key_name}: "
        + ", ".join(candidate.path for candidate in matches)
    )


def open_device(config: InputConfig, evdev_module: Any | None = None) -> Any:
    evdev = evdev_module or _import_evdev()
    return evdev.InputDevice(discover_device(config, evdev))


def _import_evdev() -> Any:
    try:
        import evdev
    except ImportError as error:
        raise DeviceDiscoveryError(
            "python-evdev is unavailable; install Fedora package python3-evdev"
        ) from error
    return evdev
