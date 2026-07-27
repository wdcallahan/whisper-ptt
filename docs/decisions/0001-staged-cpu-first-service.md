# Decision 0001: Stage a fail-closed CPU-first service

- **Date:** 2026-07-26
- **Status:** Accepted for first MACE proof

## Context

MACE already has Fedora's `python3-pywhispercpp`, PipeWire recording tools, an
active ydotool user service, a stable RØDE microphone node, an RTX 2060 without
`nvcc`, and several Lemokey evdev interfaces. Live inspection identified the
stable `usb-Keychron_Lemokey_X2-event-if01` Consumer Control link as the sole
interface advertising `KEY_MACRO28`.

The Whisper key must become useful without destabilizing the working XKB,
firmware, Meta, Any, mouse-layer, or Level5 paths.

## Decision

Build a dedicated Python user service with these properties:

- discover the one Lemokey interface advertising `KEY_MACRO28`;
- never grab the keyboard;
- require the exact configured microphone node;
- record 16 kHz mono signed 16-bit WAV through `pw-record`;
- preload Fedora's packaged pywhispercpp with `base.en` and six CPU threads;
- use Window Calls as a fail-closed focus guard;
- inject ASCII through ydotool standard input;
- keep model preparation and service activation as separate opt-in Ansible
  variables;
- verify the exact official `base.en` size and SHA-256 before model loading;
- preserve all useful artifacts on failure;
- leave the NVIDIA software stack untouched.

## Consequences

The first proof is reproducible and each risky boundary can be tested alone.
It cannot yet insert arbitrary Unicode. It depends on the enabled Window Calls
GNOME extension. CPU latency remains a measurement rather than an assumption.

The service will not substitute a webcam microphone when the RØDE is missing.
Switching to the future Mackie soundboard requires an explicit configuration
change and revalidation.

Webcam-source suppression is a later desktop-wide WirePlumber policy. It is not
the same mechanism as Kikazaru, which is the emergency all-input shutdown, and
neither is folded into this service.
