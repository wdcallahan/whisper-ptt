from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any, Callable

from .config import FocusConfig


class FocusError(RuntimeError):
    """Raised when focus cannot be established without guessing."""


@dataclass(frozen=True)
class FocusToken:
    window_id: int
    wm_class: str


class WindowCallsFocusGuard:
    DESTINATION = "org.gnome.Shell"
    OBJECT_PATH = "/org/gnome/Shell/Extensions/Windows"
    INTERFACE = "org.gnome.Shell.Extensions.Windows"

    def __init__(
        self,
        config: FocusConfig,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.config = config
        self._runner = runner

    def _call(self, method: str, argument: str | None = None) -> Any:
        command = [
            self.config.dbus_send,
            "--session",
            "--print-reply=literal",
            f"--dest={self.DESTINATION}",
            self.OBJECT_PATH,
            f"{self.INTERFACE}.{method}",
        ]
        if argument is not None:
            command.append(argument)
        try:
            result = self._runner(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.config.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise FocusError(f"Window Calls D-Bus request failed: {error}") from error
        if result.returncode != 0:
            raise FocusError(
                f"Window Calls {method} failed with status {result.returncode}: "
                f"{result.stderr.strip()}"
            )
        try:
            return json.loads(result.stdout.strip())
        except json.JSONDecodeError as error:
            raise FocusError(
                f"Window Calls {method} returned invalid JSON"
            ) from error

    def capture(self) -> FocusToken:
        windows = self._call("List")
        if not isinstance(windows, list):
            raise FocusError("Window Calls List did not return an array")

        focused: list[dict[str, Any]] = [
            window
            for window in windows
            if isinstance(window, dict) and window.get("focus") is True
        ]
        if not focused:
            for window in windows:
                if not isinstance(window, dict) or not isinstance(window.get("id"), int):
                    continue
                details = self._call("Details", f"uint32:{window['id']}")
                if isinstance(details, dict) and details.get("focus") is True:
                    focused.append(details)

        if len(focused) != 1:
            raise FocusError(
                f"expected exactly one focused GNOME window, found {len(focused)}"
            )
        window_id = focused[0].get("id")
        if not isinstance(window_id, int):
            raise FocusError("focused GNOME window has no integer id")
        return FocusToken(
            window_id=window_id,
            wm_class=str(focused[0].get("wm_class", "")),
        )

    def require_same(self, original: FocusToken) -> None:
        current = self.capture()
        if current.window_id != original.window_id:
            raise FocusError(
                "focused window changed since recording began "
                f"(was {original.window_id} {original.wm_class!r}, "
                f"now {current.window_id} {current.wm_class!r})"
            )
