"""Deploy the FibreOps **hosted agent** to Foundry Agent Service (V1Preview).

This mirrors ``agent.yaml`` programmatically via the ``azure-ai-projects`` 2.1.0
SDK: it registers the container image as an immutable hosted-agent *version*,
which triggers Foundry to provision a per-session sandbox and a dedicated Entra
agent identity. See
https://learn.microsoft.com/azure/foundry/agents/how-to/deploy-hosted-agent.

Prerequisites (performed outside this module — they touch the cloud / cost
money, so they are intentionally NOT run automatically):

1. Build the image: ``docker build --platform linux/amd64
   -f src/fibreops/agents/Dockerfile.hosted -t <acr>/fibreops-outage-response:v1 .``
2. Push it: ``az acr login --name <acr>`` then ``docker push <acr>/...:v1``.
3. Grant the project managed identity *Container Registry Repository Reader* on
   the ACR (the platform pulls the image).

Then: ``FIBREOPS_HOSTED_IMAGE=<acr>/fibreops-outage-response:v1
python -m fibreops.demo deploy-hosted``.
"""
from __future__ import annotations

import time
from typing import Any

from ..config import get_settings
from ..observability import get_logger

logger = get_logger(__name__)


def _project_client() -> Any:
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    settings = get_settings()
    if not settings.azure_ai_project_endpoint:
        raise RuntimeError("AZURE_AI_PROJECT_ENDPOINT must be set to deploy a hosted agent")
    return AIProjectClient(
        endpoint=settings.azure_ai_project_endpoint,
        credential=DefaultAzureCredential(),
        allow_preview=True,
    )


def build_hosted_definition(image: str) -> Any:
    """Build the V1Preview :class:`HostedAgentDefinition` for the image."""
    from azure.ai.projects.models import (
        AgentProtocol,
        ContainerConfiguration,
        HostedAgentDefinition,
        ProtocolVersionRecord,
    )

    settings = get_settings()
    return HostedAgentDefinition(
        protocol_versions=[
            ProtocolVersionRecord(protocol=AgentProtocol.RESPONSES, version="1.0.0")
        ],
        cpu=settings.hosted_agent_cpu,
        memory=settings.hosted_agent_memory,
        container_configuration=ContainerConfiguration(image=image),
        environment_variables={
            "MODEL_DEPLOYMENT_NAME": settings.azure_ai_model_deployment,
            "FIBREOPS_FOUNDRY_IQ": "true",
        },
    )


def _status_of(version_info: Any) -> str:
    if isinstance(version_info, dict):
        return str(version_info.get("status", "unknown"))
    return str(getattr(version_info, "status", "unknown"))


def deploy_hosted_agent(
    *,
    image: str | None = None,
    wait: bool = True,
    poll_interval: float = 5.0,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """Create (or version) the hosted agent and optionally poll until active."""
    settings = get_settings()
    image = image or settings.hosted_agent_image
    if not image:
        raise RuntimeError(
            "Set FIBREOPS_HOSTED_IMAGE (or pass image=) to the full ACR image "
            "reference, e.g. myacr.azurecr.io/fibreops-outage-response:v1"
        )

    pc = _project_client()
    name = settings.hosted_agent_name
    logger.info("deploying hosted agent", extra={"agent": name, "image": image})
    details = pc.agents.create_version(
        agent_name=name,
        definition=build_hosted_definition(image),
        description="FibreOps Outage Response Agent System (hosted)",
    )
    version = getattr(details, "version", None) or (
        details.get("version") if isinstance(details, dict) else None
    )
    result: dict[str, Any] = {"agent_name": name, "version": version, "image": image, "status": "creating"}

    if not wait:
        return result

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = pc.agents.get_version(agent_name=name, agent_version=version)
        status = _status_of(info)
        result["status"] = status
        logger.info("hosted agent status", extra={"agent": name, "version": version, "status": status})
        if status == "active":
            break
        if status == "failed":
            err = info.get("error") if isinstance(info, dict) else getattr(info, "error", None)
            result["error"] = err
            break
        time.sleep(poll_interval)
    return result


def cleanup_hosted_agent() -> dict[str, Any]:
    """Delete the hosted agent and all its versions."""
    settings = get_settings()
    pc = _project_client()
    name = settings.hosted_agent_name
    pc.agents.delete(agent_name=name)
    logger.info("deleted hosted agent", extra={"agent": name})
    return {"agent_name": name, "deleted": True}
