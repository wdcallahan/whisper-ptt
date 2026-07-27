# Nova Whisper push-to-talk

`nova-whisper-ptt` turns Nova's dedicated Whisper key into local,
release-to-finalize dictation on Fedora GNOME/Wayland.

The physical transport is:

```text
PB_28 → KEY_MACRO28 → evdev listener → PipeWire → pywhispercpp → ydotool
```

Press begins recording. Holding continues one recording and ignores repeat
events. Release finalizes the WAV, transcribes it locally, verifies that window
focus did not change, and inserts the final text once. Accepted transcripts end
with one space so consecutive push-to-talk utterances remain separate.

Every shell command in this repository is intentionally one physical line.

## Current status

Version `0.1.0` is the accepted CPU-first implementation on MACE:

- Fedora 44
- GNOME Shell 50 on Wayland
- AMD Ryzen 5 5600G, six physical cores
- RØDE NT-USB Mini
- Lemokey X2 `KEY_MACRO28` on its stable Consumer Control by-ID interface
- Fedora `python3-pywhispercpp` 1.4
- the existing user `ydotool.service`
- the enabled Window Calls GNOME extension

The repository defaults for a new workstation remain intentionally staged:

- Ansible installs and validates files but does **not** enable the service.
- Model download is a separate explicit action.
- The downloaded `base.en` model must match its pinned size and SHA-256.
- Service activation is a second separate explicit action.
- CPU `base.en` is the accepted engine; the available RTX 2060 is untouched.

MACE passed the staged desktop acceptance, persistent activation, an unchanged
Ansible deployment, reboot acceptance, live annotation suppression, and the
focus safeguards on 2026-07-26. Concurrent classroom-conferencing capture
remains an explicit operational follow-up. A different workstation is not
accepted until the same desktop runbook passes there.

## Safety properties

| Risk | Behavior |
| --- | --- |
| Wrong microphone | One exact PipeWire `node.name` is required. Missing RØDE means failure; the webcam is never a fallback. |
| Key repeat | Linux repeat events are ignored. |
| Lost release | Device loss aborts capture; a 180-second ceiling stops an orphaned recording. |
| Overlapping inference | A press while busy is rejected visibly. |
| Focus changes | A mismatch before injection blocks all typing. A second check after `ydotool` finishes detects and warns when text may have followed focus across windows; emitted characters cannot be recalled. |
| Empty/short audio | Nothing is injected. A tap, empty result, or annotation-only result becomes a desktop notification. |
| Whisper annotations | A whole-result subtitle/control cue such as `[BLANK_AUDIO]`, `[Music]`, `(silence)`, or `<\|nospeech\|>` is classified as non-speech, shown in a notification, and never typed. Mixed ordinary speech is not silently rewritten. |
| Consecutive utterances | Normalization appends exactly one trailing ASCII space so sentences do not collide. |
| Unicode ambiguity | The first proof maps common smart punctuation to ASCII and rejects all remaining non-ASCII text. |
| Damaged model | Startup verifies the official `base.en` byte count and SHA-256 before loading it. |
| Shell interpretation | Transcript bytes go to `ydotool type --file=- --escape=0` over standard input; no shell evaluates them. |
| Failure | Failures before injection emit nothing. A detected post-injection focus change may have emitted text; it raises an attention notification and retains WAV, transcripts, and failure details. |

Successful audio is removed by default. Aggregate timing metrics contain no
transcript text.

## Repository layout

```text
.
├── bin/nova-whisper-ptt
├── src/nova_whisper_ptt
├── tests
├── roles/nova_whisper_ptt
├── playbook.yml
└── docs
```

The command exposes deliberately separable proofs:

```text
doctor
list-inputs
probe-key
prepare-model
record-proof
transcribe
inject
daemon
state
```

Only `inject` and `daemon` can synthesize keyboard input. `doctor`,
`list-inputs`, `probe-key`, `record-proof`, and `transcribe` cannot.

## Development validation

Run unit tests:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Check Ansible syntax:

```bash
ansible-playbook --syntax-check playbook.yml
```

The normal deployment runbook is
[docs/runbooks/first-mace-acceptance.md](docs/runbooks/first-mace-acceptance.md).
It stops after every boundary that could reveal a wrong assumption.

## Configuration

MACE's current approved source is:

```text
alsa_input.usb-R__DE_Microphones_R__DE_NT-USB_Mini_14B577D5-00.mono-fallback
```

The webcam and motherboard capture nodes are intentionally absent from the
configuration. When the Mackie soundboard replaces the RØDE as the computer's
sole useful capture device, change only `nova_whisper_ptt_audio_source` to the
Mackie's stable PipeWire node name and rerun Ansible. The service will restart
only when activation was explicitly requested.

The first model path is:

```text
~/.local/share/nova-whisper-ptt/models/ggml-base.en.bin
```

Model construction happens at service startup so the model is warm before the
Whisper key is pressed. Startup refuses a missing model rather than downloading
one silently. It also refuses a model that does not match the official
147,964,211-byte `base.en` artifact and SHA-256
`a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002`.

This `.en` artifact is deliberately English-only. Apparent recognition of
short French phrases is incidental and is not multilingual support.

Ansible installs only the package's top-level Python source files, never local
`__pycache__` bytecode. Managed source, command, configuration, or unit changes
notify handlers. The service restarts only when such a change occurred and it
was already active, or when activation was explicitly requested. A no-op run
does not restart the daemon.

## Microphone-policy boundary

Whisper's source rule is intentionally narrower than desktop-wide microphone
policy. This service accepts one named source and fails closed when it is
absent. It never chooses the current default.

A future WirePlumber policy may hide or disable webcam capture nodes so
conferencing applications cannot select an underwater-sounding camera
microphone after an update. That everyday policy is separate from Kikazaru,
the three-monkeys emergency control that disables **all** audio inputs and
requires an explicit manual reset. Neither policy is implemented by this
repository.

## Runtime state and diagnostics

Current state:

```bash
nova-whisper-ptt state
```

Service status and journal:

```bash
systemctl --user status nova-whisper-ptt.service --no-pager && journalctl --user-unit nova-whisper-ptt.service --since today --no-pager
```

Failed utterances:

```text
~/.local/state/nova-whisper-ptt/utterances/
```

Privacy-preserving timing history:

```text
~/.local/state/nova-whisper-ptt/metrics.jsonl
```

Runtime failures, including focus changes before or after injection, enter the
Error state and raise an eight-second attention notification. Non-speech
annotations and too-short taps return to Idle and raise a shorter informational
notification instead.

## Immediate rollback

Stop and disable only this service:

```bash
systemctl --user disable --now nova-whisper-ptt.service
```

That command does not alter XKB, QMK, the Any Key, the semantic Meta adapter,
the Level5 sentinel, PipeWire, or `ydotoold`.

## Related architecture

The whole keyboard system is documented in
[`x1_keyboard_layout`](https://github.com/wdcallahan/x1_keyboard_layout).
That repository owns the keyboard identity and cross-project architecture;
this repository owns capture, transcription, focus safety, and text injection.

## License

GPL-3.0-or-later. See [COPYING](COPYING).
