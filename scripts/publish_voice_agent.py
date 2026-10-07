"""Create a separate Foundry Voice Agent Preview version for FibreOps.

Run with requirements-voice.txt in an isolated environment; the NOC runtime
still uses azure-ai-projects 2.6 through Microsoft Agent Framework.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    RealtimeFunctionToolParameters,
    VoiceAgentAudioConfig,
    VoiceAgentAudioOutputConfig,
    VoiceAgentDefinition,
    VoiceAgentFunctionTool,
    VoiceModelType,
    VoiceOutputModality,
    VoiceType,
)
from azure.identity import DefaultAzureCredential


def build_definition(
    model: str = "gpt-realtime",
    voice: str = "en-GB-OllieMultilingualNeural",
) -> VoiceAgentDefinition:
    source = Path(__file__).resolve().parent.parent / "src/fibreops/voice_live/definition.json"
    spec = json.loads(source.read_text(encoding="utf-8"))
    return VoiceAgentDefinition(
        model_type=VoiceModelType.MANAGED,
        model=model,
        instructions=spec["instructions"],
        audio=VoiceAgentAudioConfig(
            output=VoiceAgentAudioOutputConfig(
                voice=voice,
                voice_type=VoiceType.AZURE_STANDARD,
                prefer_locales=["pl-PL", "en-GB"],
            ),
        ),
        output_modalities=[VoiceOutputModality.AUDIO],
        tools=[
            VoiceAgentFunctionTool(
                name=tool["name"],
                description=tool["description"],
                parameters=RealtimeFunctionToolParameters(tool["parameters"]),
            )
            for tool in spec["tools"]
        ],
        store=False,
    )


def main() -> None:
    endpoint = os.environ["AZURE_AI_PROJECT_ENDPOINT"]
    agent_name = os.environ["AZURE_VOICE_AGENT_NAME"]
    definition = build_definition(
        model=os.environ.get("AZURE_VOICE_AGENT_MODEL", "gpt-realtime"),
        voice=os.environ.get("AZURE_VOICE_AGENT_VOICE", "en-GB-OllieMultilingualNeural"),
    )
    with (
        DefaultAzureCredential() as credential,
        AIProjectClient(endpoint=endpoint, credential=credential, allow_preview=True) as client,
    ):
        version = client.agents.create_version(agent_name=agent_name, definition=definition)
        print(f"Published Foundry voice agent {agent_name} version {version.version}")
        print(version.version)


if __name__ == "__main__":
    main()
