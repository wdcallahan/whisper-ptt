from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Callable

from .config import InjectionConfig


class InjectionError(RuntimeError):
    """Raised rather than injecting ambiguous or unsupported text."""


_FOCUS_GUARD_CHUNK_CHARACTERS = 8


_ASCII_TRANSLATION = str.maketrans(
    {
        "\u00a0": " ",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2026": "...",
    }
)


def normalize_text(text: str, config: InjectionConfig) -> str:
    normalized = re.sub(r"\s+", " ", text.translate(_ASCII_TRANSLATION)).strip()
    if not normalized:
        return ""
    if config.ascii_only:
        try:
            normalized.encode("ascii")
        except UnicodeEncodeError as error:
            offending = sorted({char for char in normalized if ord(char) > 127})
            raise InjectionError(
                "transcript contains characters outside the accepted ASCII proof: "
                + " ".join(f"U+{ord(char):04X}" for char in offending)
            ) from error
    if config.trailing_space:
        normalized += " "
    return normalized


@dataclass(frozen=True)
class InjectionResult:
    character_count: int


class YdotoolInjector:
    def __init__(
        self,
        config: InjectionConfig,
        runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    ) -> None:
        self.config = config
        self._runner = runner

    def inject(
        self,
        text: str,
        before_chunk: Callable[[int], None] | None = None,
    ) -> InjectionResult:
        if not self.config.enabled:
            raise InjectionError("text injection is disabled by configuration")
        normalized = normalize_text(text, self.config)
        if not normalized:
            return InjectionResult(character_count=0)

        chunk_size = (
            _FOCUS_GUARD_CHUNK_CHARACTERS
            if before_chunk is not None
            else len(normalized)
        )
        character_count = 0
        for offset in range(0, len(normalized), chunk_size):
            chunk = normalized[offset : offset + chunk_size]
            if before_chunk is not None:
                before_chunk(character_count)
            try:
                result = self._runner(
                    [
                        self.config.ydotool,
                        "type",
                        f"--key-delay={self.config.key_delay_ms}",
                        f"--key-hold={self.config.key_hold_ms}",
                        "--file=-",
                        "--escape=0",
                    ],
                    input=chunk.encode("ascii"),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    timeout=self.config.timeout_seconds,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise InjectionError(f"ydotool injection failed: {error}") from error
            if result.returncode != 0:
                stderr = result.stderr.decode("utf-8", errors="replace").strip()
                raise InjectionError(
                    f"ydotool exited with status {result.returncode}: {stderr}"
                )
            character_count += len(chunk)
        return InjectionResult(character_count=character_count)
