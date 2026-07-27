from __future__ import annotations

import unittest
from types import SimpleNamespace

from nova_whisper_ptt.config import InputConfig
from nova_whisper_ptt.devices import discover_device, inspect_candidates


class FakeDevice:
    def __init__(self, path: str) -> None:
        self.path = path
        self.name = "Keychron Lemokey X2"
        self.phys = "usb-test/input1"

    def capabilities(self) -> dict[int, list[int]]:
        return {1: [683]}

    def close(self) -> None:
        pass


class DeviceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evdev = SimpleNamespace(
            InputDevice=FakeDevice,
            ecodes=SimpleNamespace(EV_KEY=1, KEY_MACRO28=683),
        )

    def test_selects_only_device_advertising_macro28(self) -> None:
        config = InputConfig(device="/dev/input/fake")
        self.assertEqual(discover_device(config, self.evdev), "/dev/input/fake")
        [candidate] = inspect_candidates(config, self.evdev)
        self.assertTrue(candidate.has_key)
        self.assertTrue(candidate.readable)
