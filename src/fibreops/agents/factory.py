"""Agent factory.

Three backends are supported, selected by ``FIBREOPS_AGENT_BACKEND`` or
auto-detected:

* ``hosted``  — :class:`agent_framework_foundry.FoundryAgent` bound to a
  Prompt Agent that has been published to Microsoft Foundry via
  :mod:`fibreops.agents.publisher`. This is the architecture-diagram path:
  agents are hosted in Foundry Agent Service, the runtime supplies the Python
  tool implementations.
* ``foundry`` — :class:`agent_framework.Agent` + :class:`FoundryChatClient`.
  Definition lives only in this process; useful for iterating on prompts
  without publishing.
* ``local``   — deterministic :class:`LocalAgent` shim, no LLM. Guarantees the
  demo always runs even with zero Azure credentials.

All three honour the same ``await agent.run(prompt) -> response`` contract so
the orchestrator code is identical.
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Callable

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
from .instructions import (
    FIELD_DISPATCH_INSTRUCTIONS_V1,
    INCIDENT_ANALYSIS_INSTRUCTIONS_V1,
    NETOPS_COORDINATOR_INSTRUCTIONS_V1,
)
from .publisher import AGENT_NAMES, is_fully_published, load_registry
from .routines import NetOpsRoutineAgent

logger = get_logger(__name__)


class AgentBackend:
    HOSTED = "hosted"
    FOUNDRY = "foundry"
    LOCAL = "local"


@dataclass
class LocalAgentResponse:
    text: str
    metadata: dict[str, Any]


class LocalAgent:
    """Deterministic fallback that executes role logic without an LLM.

    Honours the same `.run(prompt)` contract as a Microsoft Agent Framework
    Agent so the orchestrator code is identical in both paths.
    """

    def __init__(self, name: str, role: str, tools: dict[str, Callable[..., Any]]):
        self.name = name
        self.role = role
        self.tools = tools

    async def run(self, prompt: str, **_: Any) -> LocalAgentResponse:
        payload = json.loads(prompt) if prompt.lstrip().startswith("{") else {"prompt": prompt}
        if self.role == "incident_analysis":
            return await self._run_incident_analysis(payload)
        if self.role == "netops_coordinator":
            return await self._run_coordinator(payload)
        if self.role == "field_dispatch":
            return await self._run_dispatch(payload)
        raise ValueError(f"Unknown role: {self.role}")

    async def _run_incident_analysis(self, p: dict[str, Any]) -> LocalAgentResponse:
        sop = await asyncio.to_thread(self.tools["lookup_sop"], signal_type=p["signal_type"])
        prior = await asyncio.to_thread(
            self.tools["recall"], scope="global", key=f"prior_incidents_for_node:{p['node_id']}"
        )
        customers = p.get("customers_served", 0)
        severity = p["severity"]
        if severity == "high" and customers > 5000:
            severity = "critical"
        # Foundry IQ grounding (BRK241 slide 9). Best-effort: never block the
        # analysis if IQ is offline or the env flag is off.
        web_hits: list[dict[str, Any]] = []
        work_hits: list[dict[str, Any]] = []
        if get_settings().foundry_iq_enabled:
            try:
                if "web_iq_search" in self.tools:
                    web_hits = await asyncio.to_thread(
                        self.tools["web_iq_search"],
                        query=f"{p.get('region','')} {p['signal_type']} outage",
                        limit=2,
                    )
                if "work_iq_search" in self.tools:
                    work_hits = await asyncio.to_thread(
                        self.tools["work_iq_search"],
                        query=f"{p['node_id']} SLA customers",
                        limit=2,
                    )
            except Exception:  # pragma: no cover - IQ is best-effort
                pass
        analysis = {
            "summary": f"{p['signal_type']} on {p['node_id']} ({p.get('site','?')}, {p.get('region','?')})",
            "probable_cause": _probable_cause(p["signal_type"], p.get("raw", {})),
            "customer_impact": f"~{customers:,} customers potentially affected",
            "severity": severity,
            "recommended_actions": _actions_for(p["signal_type"]),
            "sop_refs": [sop["id"]],
            "prior_incidents_count": len(prior),
            "knowledge": {
                "web_iq": web_hits,
                "work_iq": work_hits,
            },
        }
        return LocalAgentResponse(text=json.dumps(analysis), metadata={"role": self.role})

    async def _run_coordinator(self, p: dict[str, Any]) -> LocalAgentResponse:
        analysis = p["analysis"]
        ticket = await asyncio.to_thread(
            self.tools["create_ticket"],
            incident_id=p["incident_id"],
            node_id=p["node_id"],
            severity=analysis["severity"],
            title=f"[{analysis['severity'].upper()}] {analysis['summary']}",
            description=f"{analysis['probable_cause']}\n\nImpact: {analysis['customer_impact']}\nRecommended: {'; '.join(analysis['recommended_actions'])}",
        )
        await asyncio.to_thread(
            self.tools["post_outage_notice"],
            incident_id=p["incident_id"],
            node_id=p["node_id"],
            severity=analysis["severity"],
            summary=analysis["summary"],
            customer_impact=analysis["customer_impact"],
            probable_cause=analysis["probable_cause"],
        )
        await asyncio.to_thread(
            self.tools["remember"],
            scope="global",
            key=f"last_ticket_for_node:{p['node_id']}",
            value={"ticket_id": ticket["ticket_id"], "severity": analysis["severity"]},
        )
        if get_settings().voice_updates_enabled and "speak_status_update" in self.tools:
            try:
                await asyncio.to_thread(
                    self.tools["speak_status_update"],
                    incident_id=p["incident_id"],
                    phrase="outage_detected",
                    severity=analysis["severity"],
                    node_id=p["node_id"],
                    region=p.get("region", "?"),
                    customers=p.get("customers_served", 0),
                    probable_cause=analysis["probable_cause"],
                )
            except Exception:  # pragma: no cover - voice is best-effort
                pass
        decision = "HANDOFF:DISPATCH" if analysis["severity"] in ("high", "critical") else "MONITOR"
        reason = "auto-dispatch threshold met" if decision.startswith("HANDOFF") else "below dispatch threshold"
        return LocalAgentResponse(
            text=f"{decision} {reason}",
            metadata={"ticket": ticket, "decision": decision},
        )

    async def _run_dispatch(self, p: dict[str, Any]) -> LocalAgentResponse:
        sop = await asyncio.to_thread(self.tools["lookup_sop"], signal_type=p["signal_type"])
        skills = _skills_from_sop(sop["text"])
        result = await asyncio.to_thread(
            self.tools["dispatch_engineer"],
            incident_id=p["incident_id"],
            node_id=p["node_id"],
            required_skills=skills,
        )
        if not result.get("dispatched"):
            return LocalAgentResponse(
                text=f"NO_ENGINEER_AVAILABLE {result.get('reason','unknown')}",
                metadata={"dispatch": result},
            )
        await asyncio.to_thread(
            self.tools["post_status_update"],
            incident_id=p["incident_id"],
            status="engineer_dispatched",
            note=f"Engineer en route to {p['node_id']}",
            engineer_name=result["engineer_name"],
            eta_minutes=result["eta_minutes"],
        )
        await asyncio.to_thread(
            self.tools["update_ticket"],
            ticket_id=p["ticket_id"],
            status="assigned",
            assignee=result["engineer_name"],
        )
        # Emit a Voice Live announcement so the operator hears the dispatch.
        # Off by default (FIBREOPS_VOICE_UPDATES); always reachable from the UI.
        if get_settings().voice_updates_enabled and "speak_status_update" in self.tools:
            try:
                await asyncio.to_thread(
                    self.tools["speak_status_update"],
                    incident_id=p["incident_id"],
                    phrase="engineer_dispatched",
                    severity=p.get("severity", "medium"),
                    engineer=result["engineer_name"],
                    eta=result["eta_minutes"],
                )
            except Exception:  # pragma: no cover - voice is best-effort
                pass
        return LocalAgentResponse(
            text=f"DISPATCHED {result['engineer_name']} ETA {result['eta_minutes']} min",
            metadata={"dispatch": result},
        )


def _probable_cause(signal_type: str, raw: dict[str, Any]) -> str:
    if signal_type == "loss_of_light":
        return f"Total LoS — likely fibre cut or transceiver failure (last good {raw.get('last_good_dbm','?')} dBm)"
    if signal_type == "high_attenuation":
        return "Excess attenuation — possible dirty connector, micro-bend or splice degradation"
    if signal_type == "ber_degradation":
        return "Rising BER — transient interference or marginal transceiver"
    if signal_type == "node_unreachable":
        return "Management plane loss — could be fibre, power, or device fault"
    return "Unclassified anomaly"


def _actions_for(signal_type: str) -> list[str]:
    base = ["Confirm alarm via OLT poll", "Run OTDR sweep on affected segment"]
    if signal_type in ("loss_of_light", "node_unreachable"):
        base += ["Dispatch splicing-capable engineer", "Notify NOC duty manager"]
    if signal_type in ("high_attenuation", "ber_degradation"):
        base += ["Dispatch OTDR-certified engineer", "Open optical-degradation ticket"]
    return base


def _skills_from_sop(sop_text: str) -> list[str]:
    skills = []
    if re.search(r"splicing", sop_text, re.I):
        skills.append("splicing")
    if re.search(r"OTDR", sop_text):
        skills.append("OTDR")
    return skills or ["splicing"]


# ---- Foundry-backed builders ---------------------------------------------------


def _resolve_backend(prefer: str | None) -> str:
    """Pick the backend for this run.

    Explicit ``prefer`` wins; otherwise ``FIBREOPS_AGENT_BACKEND`` is honoured;
    otherwise we auto-detect: ``hosted`` when a published registry exists,
    ``foundry`` when an endpoint is configured, else ``local``.
    """
    if prefer:
        return prefer
    settings = get_settings()
    explicit = (settings.agent_backend or "auto").lower()
    if explicit != "auto":
        return explicit
    if settings.foundry_enabled and is_fully_published():
        return AgentBackend.HOSTED
    if settings.foundry_enabled:
        return AgentBackend.FOUNDRY
    return AgentBackend.LOCAL


def _make_foundry_agent(
    *,
    name: str,
    instructions: str,
    tools: list[Any],
    context_providers: list[Any] | None = None,
):
    from agent_framework import Agent
    from agent_framework_foundry import FoundryChatClient
    from azure.identity import DefaultAzureCredential

    settings = get_settings()
    if not settings.azure_ai_project_endpoint:
        raise RuntimeError("AZURE_AI_PROJECT_ENDPOINT must be set for Foundry-backed agents")

    client = FoundryChatClient(
        project_endpoint=settings.azure_ai_project_endpoint,
        model=settings.azure_ai_model_deployment,
        credential=DefaultAzureCredential(),
    )
    return Agent(
        client=client,
        instructions=instructions,
        name=name,
        tools=tools,
        context_providers=context_providers or None,
    )


def _make_hosted_agent(
    *,
    role: str,
    display_name: str,
    tools: list[Any],
    context_providers: list[Any] | None = None,
):
    """Connect to a Foundry-hosted Prompt Agent published by the publisher."""
    from agent_framework_foundry import FoundryAgent
    from azure.identity import DefaultAzureCredential

    settings = get_settings()
    if not settings.azure_ai_project_endpoint:
        raise RuntimeError("AZURE_AI_PROJECT_ENDPOINT must be set for hosted agents")
    registry = load_registry()
    if role not in registry:
        raise RuntimeError(
            f"No published hosted agent for role '{role}'. "
            "Run `python -m fibreops.demo publish` first."
        )
    entry = registry[role]
    logger.info(
        "binding hosted agent",
        extra={"role": role, "agent": entry["agent_name"], "version": entry["version"]},
    )
    return FoundryAgent(
        project_endpoint=settings.azure_ai_project_endpoint,
        agent_name=entry["agent_name"],
        agent_version=entry["version"],
        credential=DefaultAzureCredential(),
        tools=tools,
        name=display_name,
        context_providers=context_providers or None,
    )


def _build(
    role: str,
    display_name: str,
    instructions: str,
    tool_fns: dict[str, Callable[..., Any]],
    prefer: str | None,
):
    backend = _resolve_backend(prefer)
    tools_list: list[Any] = list(tool_fns.values())
    if backend in (AgentBackend.HOSTED, AgentBackend.FOUNDRY):
        # Hosted Foundry toolbox tools (web_search, code_interpreter, …) are
        # merged with the in-process Python tools when FIBREOPS_FOUNDRY_TOOLBOX
        # is enabled; otherwise this is a no-op.
        tools_list = tools_list + build_toolbox_tools(role)
        context_providers = build_memory_providers()
    if backend == AgentBackend.HOSTED:
        logger.info(f"creating hosted {display_name}")
        return _make_hosted_agent(
            role=role,
            display_name=display_name,
            tools=tools_list,
            context_providers=context_providers,
        )
    if backend == AgentBackend.FOUNDRY:
        logger.info(f"creating Foundry-backed {display_name}")
        return _make_foundry_agent(
            name=display_name,
            instructions=instructions,
            tools=tools_list,
            context_providers=context_providers,
        )
    logger.info(f"creating LocalAgent {display_name}")
    return LocalAgent(display_name, role, tool_fns)


def build_incident_analysis_agent(prefer: str | None = None):
    return _build(
        role="incident_analysis",
        display_name="IncidentAnalysisAgent",
        instructions=INCIDENT_ANALYSIS_INSTRUCTIONS_V1,
        tool_fns={
            "lookup_sop": lookup_sop,
            "list_sops_tool": list_sops_tool,
            "recall": recall,
            "remember": remember,
            "web_iq_search": web_iq_search,
            "work_iq_search": work_iq_search,
        },
        prefer=prefer,
    )


def build_netops_coordinator_agent(prefer: str | None = None, *, prefer_routine: bool | None = None):
    """Build the NetOps coordinator agent.

    When ``prefer_routine`` (or the ``FIBREOPS_NETOPS_ROUTINE`` env flag) is
    truthy, returns a :class:`NetOpsRoutineAgent` — a deterministic Foundry
    Routine runner — instead of the chat-style agent. This is what backs the
    BRK241 "Routines in Foundry Agent Service" announcement (slide 11).
    """
    settings = get_settings()
    use_routine = settings.netops_routine_enabled if prefer_routine is None else prefer_routine
    tool_fns: dict[str, Callable[..., Any]] = {
        "create_ticket": create_ticket,
        "update_ticket": update_ticket,
        "post_outage_notice": post_outage_notice,
        "post_status_update": post_status_update,
        "remember": remember,
        "recall": recall,
        "speak_status_update": speak_status_update,
    }
    if use_routine:
        logger.info(
            "creating NetOpsRoutineAgent (Foundry Routine)",
            extra={"role": "netops_coordinator", "backend": "routine"},
        )
        return NetOpsRoutineAgent(tool_fns)
    return _build(
        role="netops_coordinator",
        display_name="NetOpsCoordinatorAgent",
        instructions=NETOPS_COORDINATOR_INSTRUCTIONS_V1,
        tool_fns=tool_fns,
        prefer=prefer,
    )


def build_field_dispatch_agent(prefer: str | None = None):
    return _build(
        role="field_dispatch",
        display_name="FieldDispatchAgent",
        instructions=FIELD_DISPATCH_INSTRUCTIONS_V1,
        tool_fns={
            "find_best_engineer": find_best_engineer,
            "dispatch_engineer": dispatch_engineer,
            "lookup_sop": lookup_sop,
            "update_ticket": update_ticket,
            "post_status_update": post_status_update,
            "speak_status_update": speak_status_update,
        },
        prefer=prefer,
    )
