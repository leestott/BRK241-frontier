"""Shared Foundry Agent Service integrations: hosted memory + toolbox.

Both surfaces are config-gated so the demo stays fully offline by default and
"lights up" the moment real Foundry resources are configured — no code change:

* **Procedural memory** (BRK241 slide 5 / 15) — when ``FOUNDRY_MEMORY_STORE_NAME``
  is set, :func:`build_memory_providers` returns a
  :class:`agent_framework_foundry.FoundryMemoryProvider` context provider so the
  agents read/write learned procedures in Foundry's hosted memory store. When
  unset, the agents keep using the local SQLite ``remember``/``recall`` tools.

* **Toolboxes** (BRK241 slide 5 / 9) — when ``FIBREOPS_FOUNDRY_TOOLBOX`` is true,
  :func:`build_toolbox_tools` attaches the configured Foundry toolbox MCP
  endpoint through :class:`agent_framework_foundry_hosting.FoundryToolbox`.
  Off by default — the in-process tools keep the demo offline.
"""
from __future__ import annotations

from typing import Any

from ..config import get_settings
from ..observability import get_logger

logger = get_logger(__name__)

def build_memory_providers() -> list[Any]:
    """Return Foundry hosted-memory context providers, or [] for local memory."""
    settings = get_settings()
    if not settings.foundry_memory_enabled:
        return []
    if not settings.azure_ai_project_endpoint:
        logger.warning(
            "FOUNDRY_MEMORY_STORE_NAME set but AZURE_AI_PROJECT_ENDPOINT missing; "
            "falling back to local memory"
        )
        return []
    try:
        from agent_framework.foundry import FoundryMemoryProvider
        from azure.identity import DefaultAzureCredential
    except Exception as exc:  # pragma: no cover - import guard
        logger.warning("FoundryMemoryProvider unavailable (%s); using local memory", exc)
        return []

    provider = FoundryMemoryProvider(
        project_endpoint=settings.azure_ai_project_endpoint,
        credential=DefaultAzureCredential(),
        allow_preview=True,
        memory_store_name=settings.foundry_memory_store_name,
        scope=settings.foundry_memory_scope,
    )
    logger.info(
        "attached Foundry hosted memory",
        extra={"store": settings.foundry_memory_store_name},
    )
    return [provider]


def build_toolbox_tools(role: str) -> list[Any]:
    """Return the configured hosted Foundry toolbox, or [] when disabled."""
    settings = get_settings()
    if not settings.foundry_toolbox_enabled:
        return []
    if role != "incident_analysis":
        return []
    try:
        from agent_framework_foundry_hosting import FoundryToolbox
        from azure.identity import DefaultAzureCredential
    except Exception as exc:  # pragma: no cover - import guard
        logger.warning("Foundry toolbox unavailable (%s); skipping hosted tools", exc)
        return []

    if not settings.foundry_toolbox_endpoint and not settings.foundry_toolbox_name:
        logger.warning(
            "FIBREOPS_FOUNDRY_TOOLBOX is enabled but neither TOOLBOX_ENDPOINT "
            "nor TOOLBOX_NAME is configured; skipping hosted tools"
        )
        return []
    toolbox = FoundryToolbox(
        DefaultAzureCredential(),
        url=settings.foundry_toolbox_endpoint,
        name=settings.foundry_toolbox_name,
    )
    logger.info(
        "attached Foundry toolbox",
        extra={"role": role, "name": settings.foundry_toolbox_name},
    )
    return [toolbox]
