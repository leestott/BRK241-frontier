"""Shared Foundry Agent Service integrations: hosted memory + toolbox.

Both surfaces are config-gated so the demo stays fully offline by default and
"lights up" the moment real Foundry resources are configured — no code change:

* **Procedural memory** (BRK241 slide 5 / 15) — when ``FOUNDRY_MEMORY_STORE_NAME``
  is set, :func:`build_memory_providers` returns a
  :class:`agent_framework_foundry.FoundryMemoryProvider` context provider so the
  agents read/write learned procedures in Foundry's hosted memory store. When
  unset, the agents keep using the local SQLite ``remember``/``recall`` tools.

* **Toolboxes** (BRK241 slide 5 / 9) — when ``FIBREOPS_FOUNDRY_TOOLBOX`` is true,
  :func:`build_toolbox_tools` curates each role's hosted Foundry tools
  (``web_search``, ``code_interpreter``, ``mcp``, …) through
  :func:`agent_framework_foundry.select_toolbox_tools` and merges them with the
  in-process Python tools. Off by default — the in-process tools keep the demo
  offline.
"""
from __future__ import annotations

from typing import Any

from ..config import get_settings
from ..observability import get_logger

logger = get_logger(__name__)

# Hosted Foundry toolbox tools each role may draw on. The dict-shaped specs
# match Foundry's hosted-tool schema; ``select_toolbox_tools`` curates the final
# set so a single toolbox can be filtered per role.
_ROLE_TOOLBOX: dict[str, list[dict[str, Any]]] = {
    # The incident analyst grounds reasoning with live web search alongside the
    # Web IQ / Work IQ connectors.
    "incident_analysis": [{"type": "web_search", "name": "web_search"}],
    "netops_coordinator": [],
    "field_dispatch": [],
}


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
        from agent_framework_foundry import FoundryMemoryProvider
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


def build_toolbox_tools(role: str) -> list[dict[str, Any]]:
    """Return curated hosted Foundry toolbox tools for a role, or [] when off."""
    settings = get_settings()
    if not settings.foundry_toolbox_enabled:
        return []
    catalog = _ROLE_TOOLBOX.get(role, [])
    if not catalog:
        return []
    try:
        from agent_framework_foundry import select_toolbox_tools
    except Exception as exc:  # pragma: no cover - import guard
        logger.warning("Foundry toolbox unavailable (%s); skipping hosted tools", exc)
        return []

    names = [t["name"] for t in catalog if t.get("name")]
    selected = select_toolbox_tools(catalog, include_names=names)
    logger.info(
        "curated Foundry toolbox tools",
        extra={"role": role, "count": len(selected)},
    )
    return list(selected)
