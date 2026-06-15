"""Agent system.

Three role-specialised agents built on the Microsoft Agent Framework:

  - IncidentAnalysisAgent  — converts raw telemetry into a structured incident
  - NetOpsCoordinatorAgent — orchestrator: ticket, notify, hand-off
  - FieldDispatchAgent     — picks engineer, books in D365, posts ETA

Three backends are supported (see :mod:`fibreops.agents.factory`):

  - ``hosted``  : :class:`agent_framework_foundry.FoundryAgent` connected to a
    Prompt Agent published via :mod:`fibreops.agents.publisher`. This is the
    architecture-diagram path: agents are hosted in Azure AI Foundry Agent
    Service, the runtime supplies the Python tool implementations.
  - ``foundry`` : :class:`agent_framework.Agent` + :class:`FoundryChatClient`
    with the definition resolved locally — useful while iterating.
  - ``local``   : deterministic ``LocalAgent`` shim that lets the demo run
    with zero Azure credentials.
"""
from .factory import (
    build_incident_analysis_agent,
    build_netops_coordinator_agent,
    build_field_dispatch_agent,
    AgentBackend,
)
from .publisher import (
    AGENT_NAMES,
    HOSTED_AGENT_REGISTRY,
    cleanup_all,
    is_fully_published,
    load_registry,
    publish_all,
)
from .routines import (
    NETOPS_ROUTINE_DEFINITION,
    NetOpsRoutineAgent,
    RoutineDefinition,
    RoutineStep,
)

__all__ = [
    "build_incident_analysis_agent",
    "build_netops_coordinator_agent",
    "build_field_dispatch_agent",
    "AgentBackend",
    "AGENT_NAMES",
    "HOSTED_AGENT_REGISTRY",
    "cleanup_all",
    "is_fully_published",
    "load_registry",
    "publish_all",
    "NETOPS_ROUTINE_DEFINITION",
    "NetOpsRoutineAgent",
    "RoutineDefinition",
    "RoutineStep",
]
