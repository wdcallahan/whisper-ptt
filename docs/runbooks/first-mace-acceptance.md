# First MACE acceptance

- **Version:** 0.1.0
- **Date:** 2026-07-26
- **Purpose:** prove one boundary at a time without activating the daemon early

Every command is one physical line.

## 0. Preconditions

Start in the repository and confirm that the clone is clean and current:

```bash
cd ~/src/whisper-ptt && git status --short --branch && git pull --ff-only && git log -1 --oneline
```

The expected microphone is the RØDE NT-USB Mini. The Window Calls extension
and `ydotool.service` must already be active.

## 1. Static validation

Run code tests and Ansible syntax validation:

```bash
cd ~/src/whisper-ptt && PYTHONPATH=src python3 -m unittest discover -s tests -v && ansible-playbook --syntax-check playbook.yml
```

Expected: all tests pass and Ansible prints `playbook: playbook.yml`.

## 2. Install without a model or daemon

This installs packages, code, configuration, and the dormant user unit. It
does not download a model and does not enable or start Nova Whisper:

```bash
cd ~/src/whisper-ptt && ansible-playbook playbook.yml
```

The recap must show zero failures. A second run is not required during ordinary
deployment; idempotency is a release check, not a ritual after every change.

Confirm the service is not active:

```bash
systemctl --user is-enabled nova-whisper-ptt.service 2>/dev/null || true; systemctl --user is-active nova-whisper-ptt.service 2>/dev/null || true
```

Expected: it is not enabled and not active.

## 3. Read-only doctor

Run all hardware and desktop checks except model presence:

```bash
nova-whisper-ptt doctor --allow-missing-model
```

Every line must say `PASS`. In particular:

- the stable
  `/dev/input/by-id/usb-Keychron_Lemokey_X2-event-if01` Consumer Control
  interface uniquely advertises `KEY_MACRO28`;
- the exact serial-bearing RØDE source exists;
- Window Calls reports one focused window;
- ydotoold is active.

Stop here on any failure.

## 4. Whisper-key proof

Observe one press/release without grabbing the device and without recording:

```bash
nova-whisper-ptt probe-key --timeout 20
```

Press and release the Whisper key once. Expected:

```text
PRESS
RELEASE
```

Holding may show repeat lines, explicitly labeled as ignored.

## 5. RØDE-only recording proof

Record five seconds to a new temporary file:

```bash
test ! -e /tmp/nova-whisper-rode-proof.wav && nova-whisper-ptt record-proof --seconds 5 --output /tmp/nova-whisper-rode-proof.wav
```

Speak after the command begins. Play it back:

```bash
pw-play /tmp/nova-whisper-rode-proof.wav
```

Confirm by ear that this is the RØDE and not the webcam. Stop here if the sound
is wrong, silent, or underwater.

## 6. Explicit model preparation

Download `base.en` to the managed local model path without enabling the service:

```bash
cd ~/src/whisper-ptt && ansible-playbook playbook.yml -e nova_whisper_ptt_prepare_model=true
```

This is the only step that downloads the roughly 148 MB model. Preparation
recomputes the pinned SHA-256 before reporting success.

Then run the full doctor:

```bash
nova-whisper-ptt doctor
```

Every line must say `PASS`, including the model size-and-checksum proof.

## 7. Local transcription proof

Transcribe the saved RØDE WAV and print the result without injection:

```bash
nova-whisper-ptt transcribe /tmp/nova-whisper-rode-proof.wav
```

Record the reported inference time and judge the text before proceeding.

## 8. Reviewed injection proof

This types a fixed reviewed sentence into the focused window but never presses
Enter:

```bash
nova-whisper-ptt inject "Nova Whisper injection proof."
```

Expected: the sentence appears once and remains unsubmitted.

## 9. Manual end-to-end foreground proof

Run the daemon in a dedicated terminal:

```bash
nova-whisper-ptt --verbose daemon
```

Focus a harmless text field in another window, hold Whisper while speaking one
sentence, and release it. Confirm Recording, Transcribing, and Ready
notifications replace one another, and the sentence appears once.

Change focus immediately after a second release. Expected: nothing is inserted
in either window and the error notification says focus changed. Return to the
daemon terminal and stop it with Control-C.

Inspect retained diagnostics:

```bash
find ~/.local/state/nova-whisper-ptt/utterances -maxdepth 2 -type f -printf '%p\n' | sort
```

## 10. Persistent activation

Only after the foreground proof passes:

```bash
cd ~/src/whisper-ptt && ansible-playbook playbook.yml -e nova_whisper_ptt_enable_service=true
```

Verify:

```bash
systemctl --user is-enabled nova-whisper-ptt.service && systemctl --user is-active nova-whisper-ptt.service && nova-whisper-ptt state
```

Then perform one normal dictation and one deliberate focus-change rejection.

## 11. Reboot acceptance

Reboot at a convenient point, then verify:

```bash
systemctl --user is-active nova-whisper-ptt.service && nova-whisper-ptt doctor && nova-whisper-ptt state
```

Test one ordinary sentence. Also verify Any, Meta+D in Ptyxis/tmux, Level5 B,
mouse/scroll layers, and normal typing.

## Rollback

At any point, stop and disable only Nova Whisper:

```bash
systemctl --user disable --now nova-whisper-ptt.service
```

The installed files and failed-utterance evidence remain available. No rollback
step touches QMK, XKB, GNOME's Meta or Level5 extensions, PipeWire policy, or
ydotoold.
