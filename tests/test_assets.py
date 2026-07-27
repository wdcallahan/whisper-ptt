from __future__ import annotations

import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class DeploymentAssetTests(unittest.TestCase):
    def test_config_template_renders_as_toml(self) -> None:
        template = (
            ROOT
            / "roles"
            / "nova_whisper_ptt"
            / "templates"
            / "config.toml.j2"
        ).read_text(encoding="utf-8")
        replacements = {
            "{{ nova_whisper_ptt_audio_source }}": "alsa_input.approved",
            "{{ nova_whisper_ptt_minimum_duration_ms }}": "250",
            "{{ nova_whisper_ptt_maximum_duration_seconds }}": "180",
            "{{ nova_whisper_ptt_model_name }}": "base.en",
            "{{ nova_whisper_ptt_model_path }}": "/tmp/ggml-base.en.bin",
            "{{ nova_whisper_ptt_model_size_bytes }}": "147964211",
            "{{ nova_whisper_ptt_model_sha256 }}": (
                "a03779c86df3323075f5e796cb2ce502"
                "9f00ec8869eee3fdfb897afe36c6d002"
            ),
            "{{ nova_whisper_ptt_threads }}": "6",
            "{{ nova_whisper_ptt_trailing_space | bool | lower }}": "false",
            "{{ nova_whisper_ptt_success_notification | bool | lower }}": "true",
            "{{ nova_whisper_ptt_state_dir }}": "/tmp/state",
            "{{ ansible_facts.user_uid }}": "1000",
            "{{ nova_whisper_ptt_retain_successful_audio | bool | lower }}": "false",
        }
        for source, destination in replacements.items():
            template = template.replace(source, destination)
        self.assertNotIn("{{", template)
        config = tomllib.loads(template)
        self.assertEqual(config["audio"]["source"], "alsa_input.approved")
        self.assertEqual(config["whisper"]["expected_size_bytes"], 147_964_211)
        self.assertFalse(config["runtime"]["retain_successful_audio"])

    def test_service_has_bounded_startup_retries(self) -> None:
        unit = (
            ROOT
            / "roles"
            / "nova_whisper_ptt"
            / "templates"
            / "nova-whisper-ptt.service.j2"
        ).read_text(encoding="utf-8")
        self.assertIn("StartLimitIntervalSec=30s", unit)
        self.assertIn("StartLimitBurst=5", unit)
        self.assertNotIn("StartLimitIntervalSec=0", unit)
        self.assertIn("Requires=ydotool.service", unit)
