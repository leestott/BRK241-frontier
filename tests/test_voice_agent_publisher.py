"""Run with the isolated preview SDK: python -m unittest tests.test_voice_agent_publisher."""
from __future__ import annotations

import unittest

from azure.ai.projects import models


@unittest.skipUnless(
    hasattr(models, "VoiceAgentDefinition"),
    "Requires the isolated requirements-voice.txt preview publisher environment",
)
class VoiceAgentPublisherTests(unittest.TestCase):
    def test_default_definition_uses_uae_arabic_multilingual_voice(self) -> None:
        from scripts.publish_voice_agent import build_definition

        definition = build_definition().as_dict()
        output = definition["audio"]["output"]

        self.assertEqual(definition["kind"], "voice")
        self.assertEqual(definition["model"], "gpt-realtime-2.1")
        self.assertEqual(definition["model_type"], "managed")
        self.assertEqual(output["voice"], "ar-AE-Rashid:DragonHDLatestNeural")
        self.assertEqual(output["voice_type"], "azure-standard")
        self.assertEqual(output["prefer_locales"], ["en-GB", "ar-AE"])
        self.assertIsNone(output.get("voice_locale"))
        transcription = definition["audio"]["input"]["transcription"]
        self.assertEqual(transcription["model"], "whisper-1")
        self.assertIn("English, Polish, and Arabic", transcription["prompt"])
        self.assertIn("FN-LDN-001", transcription["prompt"])
        self.assertIn("dark polls", transcription["prompt"])
        self.assertIsNone(transcription.get("language"))
        self.assertIsNone(transcription.get("languages"))
        self.assertTrue(definition["audio"]["input"]["turn_detection"]["create_response"])
        self.assertIn("APPLY INDEPENDENTLY ON EVERY USER TURN", definition["instructions"])
        self.assertIn("Answer directly without announcing the chosen language", definition["instructions"])
        self.assertIn("without adding causal links or new facts", definition["instructions"])
        self.assertIn("keep one-sentence requests to a single sentence", definition["instructions"])
        self.assertIn("Treat compact network identifiers as immutable tokens", definition["instructions"])
        self.assertIn("'FN LDN zero zero one' means 'FN-LDN-001'", definition["instructions"])
        self.assertIn("ask for confirmation instead of guessing", definition["instructions"])
        self.assertEqual(len(definition["tools"]), 5)

    def test_explicit_native_voice_does_not_add_locale_bias(self) -> None:
        from scripts.publish_voice_agent import build_definition

        definition = build_definition(
            model="test-model", voice="cedar", voice_type="openai"
        ).as_dict()
        output = definition["audio"]["output"]

        self.assertEqual(definition["model"], "test-model")
        self.assertEqual(output["voice"], "cedar")
        self.assertIsNone(output.get("prefer_locales"))
        self.assertIsNone(output.get("voice_locale"))

    def test_self_deployed_definition_uses_explicit_deployment(self) -> None:
        from scripts.publish_voice_agent import build_definition

        definition = build_definition(model_type="self_deployed").as_dict()

        self.assertEqual(definition["model"], "gpt-realtime-2.1")
        self.assertEqual(definition["model_type"], "self_deployed")

    def test_invalid_voice_type_is_rejected_before_publishing(self) -> None:
        from scripts.publish_voice_agent import build_definition

        with self.assertRaisesRegex(ValueError, "openai or azure-standard"):
            build_definition(voice_type="unsupported")
