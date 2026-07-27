# Architecture

- **Version:** 0.1.0
- **Date:** 2026-07-26
- **Status:** Accepted and persistent on MACE; CPU inference, notification,
  focus safeguards, idempotency, and reboot boundaries proved

## Boundary

The keyboard and XKB projects deliver one semantic event:
`KEY_MACRO28`. This repository begins at the evdev event and ends after one
reviewable text insertion.

It does not own firmware, XKB, general GNOME keyboard policy, conferencing
microphone choice, or the three-monkeys emergency device kill switches.

## End-to-end flow

1. The listener searches only the configured stable Lemokey Consumer Control
   path, `/dev/input/by-id/usb-Keychron_Lemokey_X2-event-if01`.
2. It opens the unique device advertising `KEY_MACRO28` without grabbing it.
3. A down event captures the focused GNOME window and starts `pw-record`.
4. Repeat events are ignored.
5. The up event changes state to Transcribing immediately and finalizes WAV.
6. A preloaded local `base.en` model produces final segments.
7. An annotation-only result such as `[BLANK_AUDIO]`, `[Music]`, `(silence)`,
   or `<|nospeech|>` becomes an informational notification and no keystrokes.
8. Text normalization collapses whitespace, maps a small documented set of
   typographic punctuation to ASCII, and appends exactly one inter-utterance
   space.
9. The focus guard verifies the same GNOME window is still focused.
10. The injector sends ASCII bytes on standard input to
   `ydotool type --file=- --escape=0`.
11. Success removes per-utterance files by default and records text-free timing
    metrics.

## States

| State | Permitted input | Exit |
| --- | --- | --- |
| Starting | None | Model loaded → Idle; failure → process exit |
| Idle | One press | Recorder started → Recording |
| Recording | Release; repeats ignored | Release → Transcribing; loss/timeout → Error |
| Transcribing | New presses rejected | Empty/annotation → Idle plus information; text → Injecting; failure → Error |
| Injecting | New presses rejected | Success → Idle; failure/focus change → Error |
| Error | New press clears and retries | Valid press → Recording |
| Stopped | None | User service restart |

## Approved-source policy

`audio.source` is required and is always passed as PipeWire `--target`. There
is no auto/default option in version 0.1.

The current value names the serial-bearing RØDE NT-USB Mini node. The shorter
no-serial name seen in WirePlumber's historical default configuration is not
used. USB camera and motherboard sources are not candidates.

If the approved source is absent:

- `doctor` fails;
- Ansible's preflight fails;
- `pw-record` is never intentionally launched against another source;
- no transcript is injected.

The future Mackie migration is a configuration change, followed by the same
doctor and recording proof. It is not automatic discovery.

This fail-closed application rule is distinct from two adjacent controls:

- a future everyday WirePlumber policy that suppresses webcam microphone nodes
  from normal desktop selection; and
- Kikazaru, the emergency three-monkeys action that shuts down every audio
  input until manually reset.

Nova Whisper owns neither control and cannot weaken either one.

## Model integrity

Model preparation is explicit and never occurs during daemon startup. Version
0.1 accepts only the official full-precision English `base.en` artifact:

| Property | Accepted value |
| --- | --- |
| Filename | `ggml-base.en.bin` |
| Bytes | `147964211` |
| SHA-256 | `a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002` |

The full hash is recomputed before model construction. A missing, partial, or
different model fails closed even when a file exists at the configured path.
Recognition outside English is neither configured nor accepted as a supported
capability; familiar foreign phrases may occasionally resemble learned English
material.

## Focus safety

Wayland intentionally prevents ordinary clients from inspecting global focus.
MACE already runs `window-calls@domandoman.xyz`, which exposes Mutter's focused
window ID over the user D-Bus.

Version 0.1 records that ID at press and compares it immediately before
injection. A mismatch at that point is an error and emits no text. It compares
the ID again after `ydotool` finishes. A post-injection mismatch becomes an
attention-required error, records the emitted character count, and warns that
text may have been split across windows.

The second comparison is an audit, not target locking: `ydotool` emits a stream
of ordinary synthetic key events to whichever Wayland surface is focused at
each moment. Already emitted characters cannot be recalled. Nova normally
watches the cursor while dictating, and no Enter or other submission key is
ever synthesized. The retained `transcript.raw.txt` and `transcript.txt` make
the result recoverable.

This dependency is checked before activation. Replacing it with a narrow
Nova-owned focus broker is a possible later hardening step, not a prerequisite
for the first MACE proof.

Every Error transition raises an eight-second attention notification. That
includes a focus mismatch before injection and the post-injection audit warning
that already-emitted text may have crossed windows. Annotation-only model
results are not errors: they return to Idle, raise a shorter informational
notification naming the suppressed annotation, and inject nothing.

## Failure artifacts

Each attempt begins in a unique directory beneath:

```text
~/.local/state/nova-whisper-ptt/utterances/
```

Depending on how far the pipeline reached, a failure can contain:

- `pw-record.stderr`
- `audio.wav`
- `transcript.raw.txt`
- `transcript.txt`
- `failure.txt`

Successful directories are removed unless
`runtime.retain_successful_audio = true`.

## Concurrency and shutdown

- The evdev listener remains responsive while inference runs in one worker.
- State and the active recorder are protected by a reentrant lock.
- A second press never queues hidden work.
- A 180-second timer aborts a recording with an error instead of allowing a
  lost release to capture indefinitely.
- MACE's PipeWire 1.6.8 `pw-record` returns status 1 after the intentional
  `SIGINT` used to finalize capture. That status is accepted only when this
  process sent the interrupt and the resulting WAV validates as complete
  16 kHz mono signed-16 PCM. A spontaneous status 1 remains an error.
- systemd `SIGTERM` stops an active recorder, prevents subsequent injection,
  waits briefly for a worker, and leaves diagnostics.
- Five failed startup attempts within 30 seconds trip systemd's start limit
  instead of creating an endless restart loop.
- Device discovery retries after unplug/replug through the stable by-ID link
  without hardcoding the current `event8`.

## Deployment lifecycle

Ansible copies the package's top-level `*.py` sources individually. Runtime
`__pycache__` files are deliberately outside deployment comparison so regenerated
bytecode cannot create perpetual false changes.

Source, command, configuration, and service-unit changes notify handlers.
Service definitions reload only after a unit change. The daemon restarts only
after a real managed-file change and only when it was already active or
activation was explicitly requested. The explicit activation task otherwise
uses `state: started`, so a no-op run leaves the running process untouched.

## Acceleration boundary

The Ryzen 5 5600G receives six inference threads, matching its physical cores.
The first model is `base.en`.

The RTX 2060 and driver remain unchanged. No CUDA compiler or CUDA-enabled
Whisper build is installed until measured release-to-text latency demonstrates
a need. A later benchmark must compare whole release-to-insertion latency, not
only model inference.
