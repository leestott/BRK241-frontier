"""Containerised **hosted agent** entrypoint — BRK241 hero.

This packages FibreOps as a *hosted agent in Foundry Agent Service* (BRK241
slides 5/9/11/17). The Microsoft Agent Framework agent is wrapped by
:class:`agent_framework_foundry_hosting.ResponsesHostServer`, which serves the
OpenAI-compatible ``POST /responses`` protocol on port 8088 — the contract
Foundry Agent Service invokes inside its secure, isolated sandbox.

It is the "Build → Package → Deploy" story from the deck:

* **Build**   — :func:`build_system_agent` assembles the *Outage Response Agent
  System*: one MAF agent with the full FibreOps tool surface (analysis,
  coordination, dispatch), Foundry hosted **memory** (when configured) and
  **toolbox** tools (when enabled).
* **Package** — ``src/fibreops/agents/Dockerfile.hosted`` builds a
  ``linux/amd64`` image that runs this module and exposes 8088.
* **Deploy**  — ``agent.yaml`` (``kind: hosted``) declares the container image,
  sandbox size and ``protocol_versions`` so the Foundry CLI / azd — or
  ``python -m fibreops.demo deploy-hosted`` (which builds the same
  ``HostedAgentDefinition`` via the azure-ai-projects SDK) — can roll it out.

Run locally::

    python -m fibreops.agents.hosted_app

The server boots even without Azure credentials (the agent only calls the model
on the first ``/responses`` request), so startup is verifiable offline.
"""
from __future__ import annotations

import os
from typing import Any

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
from .foundry_services import build_memory_providers, build_toolbox_tools

logger = get_logger(__name__)

# The Outage Response Agent System runs all three phases in one hosted agent,
# driving the tools the way the orchestrator does in-process. Keeping it
# declarative lets Foundry host it without the local pipeline code.
SYSTEM_INSTRUCTIONS = """
You are the **Outage Response Agent System** for a UK fibre telco — an
autonomous NOC operator that takes a single telemetry signal from detection all
the way to engineer dispatch. You combine three roles and MUST execute them in
order, calling tools rather than guessing.

PHASE 1 — Incident analysis:
1. `lookup_sop(signal_type=...)` for the relevant SOP.
2. `recall(scope="global", key="prior_incidents_for_node:<node_id>")` for history.
3. `web_iq_search(query=...)` (Foundry Web IQ) for external conditions and
   `work_iq_search(query=...)` (Foundry Work IQ) for SLA / rota context.
4. Determine probable cause, customer impact and a confirmed severity
   ("low"|"medium"|"high"|"critical"). Never silently downgrade severity.

PHASE 2 — Coordination:
5. `create_ticket(...)` to file a D365 Field Service ticket.
6. `post_outage_notice(...)` to publish the NOC Teams notice.
7. `remember(scope="global", key="last_ticket_for_node:<node_id>", value=<ticket_id>)`.

PHASE 3 — Dispatch (only when severity is "high" or "critical"):
8. `find_best_engineer(...)` then `dispatch_engineer(...)`.
9. `post_status_update(...)` and `update_ticket(...)` with the engineer + ETA.

Finish with a concise plain-text summary: the confirmed severity, the ticket id,
and either "DISPATCHED <engineer> ETA <n> min" or "MONITOR <reason>".
""".strip()

# Full tool surface — the union of the three role agents' tools.
_SYSTEM_TOOLS: list[Any] = [
    lookup_sop,
    list_sops_tool,
    recall,
    remember,
    web_iq_search,
    work_iq_search,
    create_ticket,
    update_ticket,
    post_outage_notice,
    post_status_update,
    speak_status_update,
    find_best_engineer,
    dispatch_engineer,
]


def build_system_agent() -> Any:
    """Build the Outage Response Agent System as a Microsoft Agent Framework agent."""
    from agent_framework import Agent
    from agent_framework_foundry import FoundryChatClient
    from azure.identity import DefaultAzureCredential

    settings = get_settings()
    if not settings.azure_ai_project_endpoint:
        raise RuntimeError(
            "AZURE_AI_PROJECT_ENDPOINT must be set to host the FibreOps agent. "
            "Foundry Agent Service injects this for the hosted container."
        )

    tools: list[Any] = list(_SYSTEM_TOOLS) + build_toolbox_tools("incident_analysis")
    client = FoundryChatClient(
        project_endpoint=settings.azure_ai_project_endpoint,
        model=settings.azure_ai_model_deployment,
        credential=DefaultAzureCredential(),
    )
    agent = Agent(
        client=client,
        instructions=SYSTEM_INSTRUCTIONS,
        name="OutageResponseAgentSystem",
        description="Autonomous fibre-outage response: analyse, coordinate, dispatch.",
        tools=tools,
        context_providers=build_memory_providers() or None,
    )
    logger.info(
        "built Outage Response Agent System",
        extra={"tools": len(tools), "endpoint": settings.azure_ai_project_endpoint},
    )
    return agent


def build_host_server(agent: Any | None = None) -> Any:
    """Wrap the agent in the Foundry hosting adapter (POST /responses)."""
    from agent_framework_foundry_hosting import ResponsesHostServer

    return ResponsesHostServer(agent or build_system_agent())


def main() -> None:
    """Container entrypoint — serve the hosted agent on port 8088."""
    settings = get_settings()
    server = build_host_server()
    # Foundry Agent Service / the container platform sets the reserved PORT env
    # var; honour it when present, otherwise fall back to the local default.
    port = None if os.environ.get("PORT") else settings.hosted_agent_port
    logger.info("starting hosted agent server", extra={"port": port or os.environ.get("PORT")})
    server.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
