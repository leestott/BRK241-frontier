"""Publish, list, and delete hosted Foundry Prompt Agents.

The Foundry hosted-agent flow has two phases:

  1. **Publish** (one-time per change): build a local ``Agent`` bound to a
     ``FoundryChatClient`` and call :func:`to_prompt_agent` to lift its
     instructions, tools and generation parameters into a
     ``PromptAgentDefinition``. Then publish via
     ``AIProjectClient.agents.create_version(agent_name=..., definition=...)``.
     This persists the agent in Microsoft Foundry as a versioned, hosted
     Prompt Agent.

  2. **Run** (every request): connect with
     ``FoundryAgent(project_endpoint, agent_name, agent_version, tools=[...])``.
     The hosted definition stores tool **schemas**, but the runtime supplies the
     Python **implementations** — so the same in-process tools (Teams, D365,
     dispatch, knowledge, memory) execute exactly as they do in local mode.

We persist a registry of ``{role: {agent_name, version}}`` to
``state/foundry_agents.json`` so subsequent demo runs auto-detect that hosted
agents exist and bind to them.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .instructions import (
    FIELD_DISPATCH_INSTRUCTIONS_V1,
    INCIDENT_ANALYSIS_INSTRUCTIONS_V1,
    NETOPS_COORDINATOR_INSTRUCTIONS_V1,
)
from ..config import get_settings
from ..observability import get_logger
from ..tools import (
    create_ticket,
    dispatch_engineer,
    find_best_engineer,
    list_sops_tool,
    lookup_sop,
    post_outage_notice,
    post_status_update,
    recall,
    remember,
    speak_status_update,
    update_ticket,
    web_iq_search,
    work_iq_search,
)

logger = get_logger(__name__)

HOSTED_AGENT_REGISTRY = Path("state/foundry_agents.json")

# Stable agent names in Foundry. These are the identifiers users see in the
# Foundry portal. Changing them requires a fresh publish.
AGENT_NAMES: dict[str, str] = {
    "incident_analysis": "fibreops-incident-analysis",
    "netops_coordinator": "fibreops-netops-coordinator",
    "field_dispatch": "fibreops-field-dispatch",
}

# Per-role tool surface. Same set used by the local factory — the publisher
# converts these to FunctionTool *declarations* on the hosted definition.
ROLE_TOOLS: dict[str, list[Any]] = {
    "incident_analysis": [
        lookup_sop,
        list_sops_tool,
        recall,
        remember,
        web_iq_search,
        work_iq_search,
    ],
    "netops_coordinator": [
        create_ticket,
        update_ticket,
        post_outage_notice,
        post_status_update,
        remember,
        recall,
        speak_status_update,
    ],
    "field_dispatch": [
        find_best_engineer,
        dispatch_engineer,
        lookup_sop,
        update_ticket,
        post_status_update,
        speak_status_update,
    ],
}

ROLE_INSTRUCTIONS: dict[str, str] = {
    "incident_analysis": INCIDENT_ANALYSIS_INSTRUCTIONS_V1,
    "netops_coordinator": NETOPS_COORDINATOR_INSTRUCTIONS_V1,
    "field_dispatch": FIELD_DISPATCH_INSTRUCTIONS_V1,
}


def _build_definition_agent(role: str):
    """Construct an ``Agent`` purely for converting to a ``PromptAgentDefinition``."""
    from agent_framework import Agent
    from agent_framework_foundry import FoundryChatClient
    from azure.identity import DefaultAzureCredential

    settings = get_settings()
    if not settings.azure_ai_project_endpoint:
        raise RuntimeError(
            "AZURE_AI_PROJECT_ENDPOINT must be set before publishing hosted agents"
        )
    client = FoundryChatClient(
        project_endpoint=settings.azure_ai_project_endpoint,
        model=settings.azure_ai_model_deployment,
        credential=DefaultAzureCredential(),
    )
    return Agent(
        client=client,
        instructions=ROLE_INSTRUCTIONS[role],
        name=AGENT_NAMES[role],
        tools=ROLE_TOOLS[role],
    )


def _project_client():
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    settings = get_settings()
    return AIProjectClient(
        endpoint=settings.azure_ai_project_endpoint,
        credential=DefaultAzureCredential(),
        allow_preview=True,
    )


def load_registry() -> dict[str, dict[str, str]]:
    if HOSTED_AGENT_REGISTRY.exists():
        try:
            return json.loads(HOSTED_AGENT_REGISTRY.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("foundry_agents.json is corrupt; ignoring")
    return {}


def save_registry(reg: dict[str, dict[str, str]]) -> None:
    HOSTED_AGENT_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    HOSTED_AGENT_REGISTRY.write_text(json.dumps(reg, indent=2), encoding="utf-8")


def is_fully_published() -> bool:
    reg = load_registry()
    return all(role in reg and reg[role].get("version") for role in AGENT_NAMES)


def publish_all() -> dict[str, dict[str, str]]:
    """Create or update hosted Prompt Agent versions for every role.

    Returns the registry mapping ``{role: {agent_name, version}}`` and persists
    it to ``state/foundry_agents.json``.
    """
    from agent_framework_foundry import to_prompt_agent

    registry = load_registry()
    pc = _project_client()
    try:
        for role, agent_name in AGENT_NAMES.items():
            local_agent = _build_definition_agent(role)
            definition = to_prompt_agent(local_agent)
            details = pc.agents.create_version(
                agent_name=agent_name,
                definition=definition,
                description=f"FibreOps {role.replace('_',' ')} agent",
            )
            version = str(getattr(details, "version", None) or getattr(details, "id", "1"))
            registry[role] = {"agent_name": agent_name, "version": version}
            logger.info(
                "published hosted agent",
                extra={"agent": agent_name, "role": role, "version": version},
            )
    finally:
        pc.close()
    save_registry(registry)
    return registry


def cleanup_all() -> list[str]:
    """Delete every hosted agent we published and wipe the local registry."""
    from azure.core.exceptions import ResourceNotFoundError

    removed: list[str] = []
    pc = _project_client()
    try:
        for agent_name in AGENT_NAMES.values():
            try:
                pc.agents.delete(agent_name=agent_name, force=True)
                removed.append(agent_name)
                logger.info("deleted hosted agent", extra={"agent": agent_name})
            except ResourceNotFoundError:
                logger.info("hosted agent already absent", extra={"agent": agent_name})
    finally:
        pc.close()
    if HOSTED_AGENT_REGISTRY.exists():
        HOSTED_AGENT_REGISTRY.unlink()
    return removed
