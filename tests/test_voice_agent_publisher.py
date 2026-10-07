"""Run with the isolated preview SDK: python -m unittest tests.test_voice_agent_publisher."""
from __future__ import annotations

import unittest

from azure.ai.projects import models


@unittest.skipUnless(
    hasattr(models, "VoiceAgentDefinition"),
    "Requires the isolated requirements-voice.txt preview publisher environment",
)
class VoiceAgentPublisherTests(unittest.TestCase):
    def test_default_definition_serializes_language_specific_pronunciation(self) -> None:
        from scripts.publish_voice_agent import build_definition

        definition = build_definition().as_dict()
        output = definition["audio"]["output"]

        self.assertEqual(definition["kind"], "voice")
        self.assertEqual(definition["model"], "gpt-realtime")
        self.assertEqual(output["voice"], "en-GB-OllieMultilingualNeural")
        self.assertEqual(output["voice_type"], "azure-standard")
        self.assertEqual(output["prefer_locales"], ["pl-PL", "en-GB"])
        self.assertIsNone(output.get("voice_locale"))
        self.assertIn("native Polish pronunciation", definition["instructions"])
        self.assertIn("Otherwise, reply in natural British English", definition["instructions"])
        self.assertEqual(len(definition["tools"]), 5)

    def test_explicit_model_and_voice_preserve_locale_preferences(self) -> None:
        from scripts.publish_voice_agent import build_definition

        definition = build_definition(
            model="test-model", voice="en-GB-AdaMultilingualNeural"
        ).as_dict()
        output = definition["audio"]["output"]

        self.assertEqual(definition["model"], "test-model")
        self.assertEqual(output["voice"], "en-GB-AdaMultilingualNeural")
        self.assertEqual(output["prefer_locales"], ["pl-PL", "en-GB"])
        self.assertIsNone(output.get("voice_locale"))
