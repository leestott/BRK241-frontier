"""Centralised, env-driven configuration."""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Foundry / AI Projects — endpoint is preferred by current SDK
    # (e.g. https://<account>.services.ai.azure.com/api/projects/<project>).
    # In a Foundry **hosted agent** sandbox the platform injects
    # ``FOUNDRY_PROJECT_ENDPOINT`` instead of ``AZURE_AI_PROJECT_ENDPOINT``, so
    # accept either name (same code runs locally and hosted).
    azure_ai_project_endpoint: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("AZURE_AI_PROJECT_ENDPOINT", "FOUNDRY_PROJECT_ENDPOINT"),
    )
    azure_ai_project_connection_string: Optional[str] = Field(default=None, alias="AZURE_AI_PROJECT_CONNECTION_STRING")
    # Foundry project name (the trailing path segment of the project endpoint,
    # e.g. ``leestott-1891`` from ``…/api/projects/leestott-1891``). Required
    # for Voice Live agent-mode URLs. Auto-derived from the endpoint when unset.
    azure_ai_project_name: Optional[str] = Field(default=None, alias="AZURE_AI_PROJECT_NAME")
    # Hosted-agent deploys conventionally pass the model as ``MODEL_DEPLOYMENT_NAME``;
    # accept that alongside ``AZURE_AI_MODEL_DEPLOYMENT``.
    azure_ai_model_deployment: str = Field(
        default="gpt-4.1-mini",
        validation_alias=AliasChoices("AZURE_AI_MODEL_DEPLOYMENT", "MODEL_DEPLOYMENT_NAME"),
    )

    # Event Hub
    event_hub_fqdn: Optional[str] = Field(default=None, alias="EVENT_HUB_FQDN")
    event_hub_name: str = Field(default="fibre-signals", alias="EVENT_HUB_NAME")
    event_hub_consumer_group: str = Field(default="$Default", alias="EVENT_HUB_CONSUMER_GROUP")

    # Teams
    teams_webhook_url: Optional[str] = Field(default=None, alias="TEAMS_WEBHOOK_URL")

    # Voice Live (Azure AI Voice Live integration with Foundry Agent Service).
    # ``azure_voice_live_endpoint`` is the realtime base URL — either an
    # ``https://<region>.api.cognitive.microsoft.com`` host or a fully-formed
    # ``wss://...`` WebSocket URL. When set, the UI opens a Voice Live session
    # via the server-side proxy at ``/ws/voice`` so the browser plays real TTS
    # audio for "Speak status" and supports duplex mic via "Talk to agent".
    # When unset, utterances append to ``state/voice_outbox.jsonl`` so the
    # demo always works offline.
    azure_voice_live_endpoint: Optional[str] = Field(default=None, alias="AZURE_VOICE_LIVE_ENDPOINT")
    azure_voice_live_api_key: Optional[str] = Field(default=None, alias="AZURE_VOICE_LIVE_API_KEY")
    azure_voice_live_voice: Optional[str] = Field(default=None, alias="AZURE_VOICE_LIVE_VOICE")
    # Foundry agent the duplex "Talk to agent" session is bound to. Optional
    # for one-shot TTS; required for duplex mic conversation.
    azure_voice_live_agent_id: Optional[str] = Field(default=None, alias="AZURE_VOICE_LIVE_AGENT_ID")
    # Realtime API version string passed as a query parameter on the upstream
    # WebSocket URL. Override if Microsoft ships a newer Voice Live preview.
    azure_voice_live_api_version: str = Field(
        default="2026-04-10", alias="AZURE_VOICE_LIVE_API_VERSION"
    )

    # Foundry IQ — knowledge grounding (BRK241 slide 4 / slide 9).
    # Two endpoints, both optional, both POST {query, limit} -> {results:[...]}.
    # When unset, the tools serve deterministic local fixtures so the demo
    # works offline; flipping in real endpoints requires zero code change.
    foundry_web_iq_endpoint: Optional[str] = Field(default=None, alias="FOUNDRY_WEB_IQ_ENDPOINT")
    foundry_web_iq_api_key: Optional[str] = Field(default=None, alias="FOUNDRY_WEB_IQ_API_KEY")
    foundry_work_iq_endpoint: Optional[str] = Field(default=None, alias="FOUNDRY_WORK_IQ_ENDPOINT")
    foundry_work_iq_api_key: Optional[str] = Field(default=None, alias="FOUNDRY_WORK_IQ_API_KEY")
    # When true (default), the IncidentAnalysisAgent enriches its analysis with
    # Web IQ + Work IQ snippets so the demo shows grounded reasoning.
    foundry_iq_enabled: bool = Field(default=True, alias="FIBREOPS_FOUNDRY_IQ")

    # Procedural memory in Foundry Agent Service (BRK241 slide 5 / slide 15).
    # When ``foundry_memory_store_name`` is set, the agents attach a
    # :class:`agent_framework_foundry.FoundryMemoryProvider` context provider so
    # learned procedures persist in Foundry's hosted memory store. When unset,
    # the local SQLite ``state/memory.db`` (remember/recall tools) is used so the
    # demo runs offline.
    foundry_memory_store_name: Optional[str] = Field(default=None, alias="FOUNDRY_MEMORY_STORE_NAME")
    foundry_memory_scope: Optional[str] = Field(default=None, alias="FOUNDRY_MEMORY_SCOPE")

    # Foundry Toolbox (BRK241 slide 5 / slide 9). When true, the factory curates
    # each role's tool surface through ``agent_framework_foundry.select_toolbox_tools``
    # so hosted Foundry toolbox tools (web search, code interpreter, MCP, …) can be
    # mixed with the in-process Python tools. Off by default — the in-process tools
    # keep the demo fully offline.
    foundry_toolbox_enabled: bool = Field(default=False, alias="FIBREOPS_FOUNDRY_TOOLBOX")

    # Agent Optimizer / Foundry Evals (BRK241 slide 5 / slide 15). When true, the
    # optimiser runs the cloud evaluators (``FoundryEvals`` / ``evaluate_traces``)
    # in addition to the local rubric so the demo shows real Foundry evaluation
    # scores. Off by default so the optimiser stays offline-safe.
    foundry_evals_enabled: bool = Field(default=False, alias="FIBREOPS_FOUNDRY_EVALS")

    # Port the containerised hosted agent (ResponsesHostServer) listens on. The
    # Foundry Agent Service / Container platform may override via the reserved
    # PORT env var; this is the local-dev default. (Foundry hosts on 8088.)
    hosted_agent_port: int = Field(default=8088, alias="FIBREOPS_HOSTED_PORT")
    # Hosted-agent deployment (V1Preview) — used by `demo deploy-hosted`.
    hosted_agent_name: str = Field(default="fibreops-outage-response", alias="FIBREOPS_HOSTED_AGENT_NAME")
    # Full ACR image reference (e.g. myacr.azurecr.io/fibreops-agent:v1) to deploy.
    hosted_agent_image: Optional[str] = Field(default=None, alias="FIBREOPS_HOSTED_IMAGE")
    hosted_agent_cpu: str = Field(default="1", alias="FIBREOPS_HOSTED_CPU")
    hosted_agent_memory: str = Field(default="2Gi", alias="FIBREOPS_HOSTED_MEMORY")

    # Microsoft 365 Copilot publishing — base URL that the declarative agent's actions
    # call back into (the FastAPI app, exposed publicly). Used by `publish-m365`.
    m365_action_base_url: Optional[str] = Field(default=None, alias="M365_ACTION_BASE_URL")
    m365_app_id: str = Field(default="fibreops-copilot-agent", alias="M365_APP_ID")
    m365_publisher_name: str = Field(default="FibreOps", alias="M365_PUBLISHER_NAME")
    m365_publisher_website: str = Field(
        default="https://example.com/fibreops", alias="M365_PUBLISHER_WEBSITE"
    )

    # Mock D365
    d365_mock_base_url: str = Field(default="http://127.0.0.1:8765", alias="D365_MOCK_BASE_URL")
    d365_mock_port: int = Field(default=8765, alias="D365_MOCK_PORT")

    # Observability
    applicationinsights_connection_string: Optional[str] = Field(default=None, alias="APPLICATIONINSIGHTS_CONNECTION_STRING")

    # Behaviour
    auto_dispatch: bool = Field(default=True, alias="FIBREOPS_AUTO_DISPATCH")
    optimiser_enabled: bool = Field(default=True, alias="FIBREOPS_OPTIMISER_ENABLED")
    # Agent backend selection: "auto" | "hosted" | "foundry" | "local"
    #   auto    -> hosted if a published registry exists, else foundry if configured, else local
    #   hosted  -> always FoundryAgent against published agent_name/version
    #   foundry -> always Agent + FoundryChatClient (definition resolved locally)
    #   local   -> deterministic LocalAgent fallback
    agent_backend: str = Field(default="auto", alias="FIBREOPS_AGENT_BACKEND")
    # When true, swap the NetOps coordinator role to a Foundry Routine
    # (declarative plan in fibreops.agents.routines). Lets the demo show the
    # "Routines in Foundry Agent Service" announcement from BRK241 slide 11.
    netops_routine_enabled: bool = Field(default=False, alias="FIBREOPS_NETOPS_ROUTINE")
    # When true, the orchestrator emits a Voice Live update after Teams
    # notices and after dispatch. Off by default so the CLI demo stays quiet;
    # the UI button always works because it calls the tool directly.
    voice_updates_enabled: bool = Field(default=False, alias="FIBREOPS_VOICE_UPDATES")

    @property
    def foundry_enabled(self) -> bool:
        return bool(self.azure_ai_project_endpoint or self.azure_ai_project_connection_string)

    @property
    def event_hub_enabled(self) -> bool:
        return bool(self.event_hub_fqdn)

    @property
    def teams_enabled(self) -> bool:
        return bool(self.teams_webhook_url)

    @property
    def voice_live_enabled(self) -> bool:
        return bool(self.azure_voice_live_endpoint)

    @property
    def web_iq_enabled(self) -> bool:
        return bool(self.foundry_web_iq_endpoint)

    @property
    def work_iq_enabled(self) -> bool:
        return bool(self.foundry_work_iq_endpoint)

    @property
    def foundry_memory_enabled(self) -> bool:
        return bool(self.foundry_memory_store_name)


@lru_cache
def get_settings() -> Settings:
    return Settings()
