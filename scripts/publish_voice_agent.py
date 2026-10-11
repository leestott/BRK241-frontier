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
    VoiceAgentAudioInputConfig,
    VoiceAgentAudioOutputConfig,
    VoiceAgentDefinition,
    VoiceAgentFunctionTool,
    VoiceAgentInputTranscription,
    VoiceAgentServerVadTurnDetection,
    VoiceModelType,
    VoiceOutputModality,
    VoiceType,
)
from azure.identity import DefaultAzureCredential


def build_definition(
    model: str = "gpt-realtime-2.1",
    model_type: str = "managed",
    voice: str = "ar-AE-Rashid:DragonHDLatestNeural",
    voice_type: str = "azure-standard",
    prefer_locales: list[str] | None = None,
) -> VoiceAgentDefinition:
    openai_voices = {"alloy", "ash", "ballad", "coral", "cedar", "echo", "marin", "sage", "shimmer", "verse"}
    if voice_type == "openai":
        if voice not in openai_voices:
            raise ValueError("OpenAI voice output requires a native realtime voice such as marin.")
        output = VoiceAgentAudioOutputConfig(
            voice=voice,
            voice_type=VoiceType.OPENAI,
        )
    elif voice_type == "azure-standard":
        output = VoiceAgentAudioOutputConfig(
            voice=voice,
            voice_type=VoiceType.AZURE_STANDARD,
            prefer_locales=prefer_locales or ["en-GB", "ar-AE"],
        )
    else:
        raise ValueError("AZURE_VOICE_AGENT_VOICE_TYPE must be openai or azure-standard.")
    source = Path(__file__).resolve().parent.parent / "src/fibreops/voice_live/definition.json"
    spec = json.loads(source.read_text(encoding="utf-8"))
    model_types = {
        "managed": VoiceModelType.MANAGED,
        "self_deployed": VoiceModelType.SELF_DEPLOYED,
    }
    if model_type not in model_types:
        raise ValueError("AZURE_VOICE_AGENT_MODEL_TYPE must be managed or self_deployed.")
    return VoiceAgentDefinition(
        model_type=model_types[model_type],
        model=model,
        instructions=spec["instructions"],
        audio=VoiceAgentAudioConfig(
            input=VoiceAgentAudioInputConfig(
                transcription=VoiceAgentInputTranscription(
                    model="whisper-1",
                    prompt=(
                        "Network operations in English, Polish, and Arabic: Dubai, cause, "
                        "unconfirmed cause, optical signal, attenuation, fibre, dark polls, "
                        "FN-LDN-001, FN-CRD-005, INC-DEMO-CRITICAL, and Bay Cabinet 5."
                    ),
                ),
                turn_detection=VoiceAgentServerVadTurnDetection(
                    silence_duration_ms=1200,
                    create_response=True,
                    interrupt_response=True,
                ),
            ),
            output=output,
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
    endpoint = os.environ.get("AZURE_VOICE_PROJECT_ENDPOINT") or os.environ[
        "AZURE_AI_PROJECT_ENDPOINT"
    ]
    agent_name = os.environ["AZURE_VOICE_AGENT_NAME"]
    definition = build_definition(
        model=os.environ.get("AZURE_VOICE_AGENT_MODEL", "gpt-realtime-2.1"),
        model_type=os.environ.get("AZURE_VOICE_AGENT_MODEL_TYPE", "managed"),
        voice=os.environ.get(
            "AZURE_VOICE_AGENT_VOICE", "ar-AE-Rashid:DragonHDLatestNeural"
        ),
        voice_type=os.environ.get("AZURE_VOICE_AGENT_VOICE_TYPE", "azure-standard"),
        prefer_locales=[
            locale.strip()
            for locale in os.environ.get(
                "AZURE_VOICE_AGENT_PREFER_LOCALES", "en-GB,ar-AE"
            ).split(",")
            if locale.strip()
        ],
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
