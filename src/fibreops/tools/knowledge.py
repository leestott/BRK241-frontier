"""Knowledge tool — SOPs + node topology lookup + Foundry IQ grounding.

The classical "Knowledge" surface for FibreOps has three layers:

1. **Internal SOPs** (markdown) and **node topology** (JSON) — local fixtures
   served by :func:`lookup_sop`, :func:`list_sops_tool`, :func:`lookup_node`.
2. **Web IQ** — Foundry's web-grounded knowledge connector. Reachable via
   :func:`web_iq_search`. POSTs to ``FOUNDRY_WEB_IQ_ENDPOINT`` when set; falls
   back to deterministic local fixtures so the demo runs offline.
3. **Work IQ** — Foundry's enterprise-grounded knowledge connector (Fabric /
   SharePoint / Graph). Reachable via :func:`work_iq_search`. POSTs to
   ``FOUNDRY_WORK_IQ_ENDPOINT`` when set; falls back to deterministic local
   fixtures.

Every IQ lookup is appended to ``state/iq_lookups.jsonl`` so the UI can
render a live "Knowledge sources" pane that shows what the agents grounded
their decisions on (BRK241 slide 9 "Foundry IQ" announcement).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import get_settings
from ..mocks import list_sops, load_json
from ..observability import tool_span, get_logger

logger = get_logger(__name__)

_IQ_OUTBOX = Path("state") / "iq_lookups.jsonl"


def list_sops_tool() -> list[dict[str, str]]:
    """Return all available Standard Operating Procedures (id, title, text)."""
    with tool_span("knowledge.list_sops"):
        return list_sops()


def lookup_sop(signal_type: str) -> dict[str, Any]:
    """Return the most relevant SOP for a given signal_type."""
    with tool_span("knowledge.lookup_sop", signal_type=signal_type):
        mapping = {
            "loss_of_light": "sop_loss_of_light",
            "node_unreachable": "sop_loss_of_light",
            "high_attenuation": "sop_attenuation",
            "ber_degradation": "sop_attenuation",
        }
        target = mapping.get(signal_type, "sop_loss_of_light")
        for doc in list_sops():
            if doc["id"] == target:
                logger.info("SOP matched", extra={"tool": "knowledge.lookup_sop"})
                return doc
        return {"id": "none", "title": "No SOP", "text": ""}


def lookup_node(node_id: str) -> dict[str, Any]:
    """Return topology metadata for a fibre node."""
    with tool_span("knowledge.lookup_node", node_id=node_id):
        for node in load_json("fibre_nodes.json"):
            if node["node_id"] == node_id:
                return node
        return {}


# --- Foundry IQ -----------------------------------------------------------------

# Local fixtures used when no IQ endpoint is configured. Keyed by lowercase
# keywords so a simple substring match keeps the offline demo realistic.
_WEB_IQ_FIXTURES: list[dict[str, Any]] = [
    {
        "keywords": ["london", "fibre", "outage"],
        "title": "Roadworks alert: A10 carriageway resurfacing — Shoreditch",
        "snippet": (
            "Transport for London notes lane closures on the A10 near Shoreditch "
            "between 22:00 and 05:00 this week. Local utility companies have "
            "reported intermittent fibre damage during similar works in 2024."
        ),
        "url": "https://example.com/tfl/roadworks/a10-shoreditch",
        "source": "tfl.gov.uk",
    },
    {
        "keywords": ["manchester", "weather", "storm"],
        "title": "Met Office: Yellow warning — heavy rain across the North-West",
        "snippet": (
            "Persistent heavy rainfall expected across Greater Manchester. "
            "Surface-water flooding may impact street-level cabinets."
        ),
        "url": "https://example.com/metoffice/warnings/yellow-rain-nw",
        "source": "metoffice.gov.uk",
    },
    {
        "keywords": ["edinburgh", "power", "outage"],
        "title": "SP Energy Networks: scheduled supply interruption — Leith",
        "snippet": (
            "Planned 03:00–06:00 supply interruption affecting EH6 postcodes "
            "for substation maintenance. Co-located comms cabinets may lose "
            "primary feed; UPS bridge ~90 minutes."
        ),
        "url": "https://example.com/spen/leith-maintenance",
        "source": "spenergynetworks.co.uk",
    },
    {
        "keywords": ["fibre", "splice", "best", "practice"],
        "title": "ITU-T G.652.D fusion splicing guidance",
        "snippet": (
            "Industry guidance recommends OTDR validation within 24h of any "
            "field splice; record attenuation delta and bidirectional loss."
        ),
        "url": "https://example.com/itu/g652d",
        "source": "itu.int",
    },
]

_WORK_IQ_FIXTURES: list[dict[str, Any]] = [
    {
        "keywords": ["fn-ldn-001", "shoreditch"],
        "title": "FN-LDN-001 — site survey, March 2026",
        "snippet": (
            "Critical metro hub. 12,400 customers, including 4 NHS trust "
            "endpoints with platinum SLA (15-minute restore). Diverse "
            "back-haul via FN-LDN-014 (Canary Wharf) and FN-LDN-008 "
            "(Stratford). Vault access requires NOC duty manager approval."
        ),
        "source": "Fabric / Network Topology Lakehouse",
        "url": "fabric://workspaces/fibreops/lakehouses/topology/items/FN-LDN-001",
        "last_modified": "2026-03-14",
    },
    {
        "keywords": ["sla", "customer", "platinum"],
        "title": "Customer SLA tiers — 2026 contract refresh",
        "snippet": (
            "Platinum tier: 15-minute notification window, on-site within "
            "60 minutes for criticals. Gold tier: 30-minute notification, "
            "on-site within 4h. Service credits accrue per outage minute "
            "beyond SLA."
        ),
        "source": "SharePoint / Commercial Ops / SLA Library",
        "url": "https://example.sharepoint.com/sites/commercial/SLA-2026.pdf",
        "last_modified": "2026-01-20",
    },
    {
        "keywords": ["splicing", "engineer", "skill"],
        "title": "Field engineer competency matrix",
        "snippet": (
            "Splicing-certified engineers: 12 across UK regions. OTDR-certified: "
            "9. On-call rotation rebalanced quarterly; current week-on-call: "
            "London (Priya Shah), Manchester (Aaron Doyle)."
        ),
        "source": "Fabric / People & Skills Lakehouse",
        "url": "fabric://workspaces/fibreops/lakehouses/people/competency",
        "last_modified": "2026-06-01",
    },
    {
        "keywords": ["mttr", "history", "trend"],
        "title": "MTTR trend — H1 2026",
        "snippet": (
            "Mean Time To Restore for critical fibre incidents has dropped "
            "from 78 min (Jan) to 54 min (May). Improvement attributed to "
            "auto-dispatch + Foundry agent orchestration go-live."
        ),
        "source": "Fabric / NOC Analytics Lakehouse",
        "url": "fabric://workspaces/fibreops/reports/mttr-h1-2026",
        "last_modified": "2026-06-10",
    },
]


def _persist_lookup(*, source: str, query: str, results: list[dict[str, Any]]) -> None:
    _IQ_OUTBOX.parent.mkdir(exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "query": query,
        "result_count": len(results),
        "results": results,
    }
    with _IQ_OUTBOX.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=0.4, min=0.4, max=3))
def _post_iq(endpoint: str, api_key: str | None, payload: dict[str, Any]) -> list[dict[str, Any]]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Ocp-Apim-Subscription-Key"] = api_key
    with httpx.Client(timeout=8.0) as client:
        response = client.post(endpoint, json=payload, headers=headers)
        response.raise_for_status()
        body = response.json()
    if isinstance(body, dict) and isinstance(body.get("results"), list):
        return body["results"]
    if isinstance(body, list):
        return body
    return []


def _match_fixtures(fixtures: list[dict[str, Any]], query: str, limit: int) -> list[dict[str, Any]]:
    q = (query or "").lower()
    scored: list[tuple[int, dict[str, Any]]] = []
    for f in fixtures:
        hits = sum(1 for kw in f.get("keywords", []) if kw in q)
        if hits:
            scored.append((hits, f))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        # Always return *something* so the agent has grounded context.
        scored = [(0, f) for f in fixtures[:limit]]
    return [
        {k: v for k, v in f.items() if k != "keywords"}
        for _, f in scored[:limit]
    ]


def web_iq_search(query: str, limit: int = 3) -> list[dict[str, Any]]:
    """Foundry Web IQ — return web-grounded snippets for a query.

    POSTs ``{query, limit}`` to ``FOUNDRY_WEB_IQ_ENDPOINT`` when configured;
    otherwise serves deterministic local fixtures so the demo always works.

    Returns a list of ``{title, snippet, url, source}`` dicts.
    """
    with tool_span("knowledge.web_iq_search", query=query):
        settings = get_settings()
        limit = max(1, min(int(limit), 10))
        if settings.web_iq_enabled:
            try:
                results = _post_iq(
                    settings.foundry_web_iq_endpoint or "",
                    settings.foundry_web_iq_api_key,
                    {"query": query, "limit": limit},
                )
            except Exception as exc:  # pragma: no cover - defensive on flaky upstream
                logger.warning("Web IQ POST failed, using fixtures: %s", exc)
                results = _match_fixtures(_WEB_IQ_FIXTURES, query, limit)
        else:
            results = _match_fixtures(_WEB_IQ_FIXTURES, query, limit)
        _persist_lookup(source="web_iq", query=query, results=results)
        return results


def work_iq_search(query: str, limit: int = 3) -> list[dict[str, Any]]:
    """Foundry Work IQ — return enterprise-grounded snippets for a query.

    POSTs ``{query, limit}`` to ``FOUNDRY_WORK_IQ_ENDPOINT`` when configured
    (Fabric / SharePoint / Graph backend); otherwise serves deterministic
    local fixtures so the demo always works.

    Returns a list of ``{title, snippet, source, url, last_modified}`` dicts.
    """
    with tool_span("knowledge.work_iq_search", query=query):
        settings = get_settings()
        limit = max(1, min(int(limit), 10))
        if settings.work_iq_enabled:
            try:
                results = _post_iq(
                    settings.foundry_work_iq_endpoint or "",
                    settings.foundry_work_iq_api_key,
                    {"query": query, "limit": limit},
                )
            except Exception as exc:  # pragma: no cover - defensive on flaky upstream
                logger.warning("Work IQ POST failed, using fixtures: %s", exc)
                results = _match_fixtures(_WORK_IQ_FIXTURES, query, limit)
        else:
            results = _match_fixtures(_WORK_IQ_FIXTURES, query, limit)
        _persist_lookup(source="work_iq", query=query, results=results)
        return results

def _knowledge_base_credential():
    """Credential for Foundry IQ retrieval: api-key if set, else managed identity."""
    settings = get_settings()
    if settings.foundry_iq_search_api_key:
        from azure.core.credentials import AzureKeyCredential

        return AzureKeyCredential(settings.foundry_iq_search_api_key)
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential()


def knowledge_base_search(query: str, limit: int = 3) -> list[dict[str, Any]]:
    """Foundry IQ — agentic retrieval over the FibreOps knowledge base.

    Queries the Azure AI Search knowledge base configured by
    ``FOUNDRY_IQ_SEARCH_ENDPOINT`` + ``FOUNDRY_IQ_KNOWLEDGE_BASE`` using
    managed-identity auth (or ``FOUNDRY_IQ_SEARCH_API_KEY`` for local dev). The
    knowledge base runs minimal-effort agentic retrieval over the FibreOps SOPs
    and node topology and returns grounded, cited extracts.

    Falls back to the deterministic Work IQ fixtures when no knowledge base is
    configured or retrieval fails, so the demo always works.

    Returns a list of ``{title, snippet, source}`` dicts.
    """
    with tool_span("knowledge.knowledge_base_search", query=query):
        settings = get_settings()
        limit = max(1, min(int(limit), 10))
        if not settings.knowledge_base_enabled:
            results = _match_fixtures(_WORK_IQ_FIXTURES, query, limit)
            _persist_lookup(source="foundry_iq", query=query, results=results)
            return results
        try:
            from azure.search.documents.knowledgebases import KnowledgeBaseRetrievalClient
            from azure.search.documents.knowledgebases.models import (
                KnowledgeBaseRetrievalRequest,
                KnowledgeRetrievalSemanticIntent,
            )

            client = KnowledgeBaseRetrievalClient(
                endpoint=settings.foundry_iq_search_endpoint or "",
                knowledge_base_name=settings.foundry_iq_knowledge_base or "",
                credential=_knowledge_base_credential(),
            )
            # Minimal reasoning effort requires `intents` (not chat messages).
            response = client.retrieve(
                KnowledgeBaseRetrievalRequest(
                    intents=[KnowledgeRetrievalSemanticIntent(search=query)]
                )
            )
            data = response.as_dict()
            kb_name = settings.foundry_iq_knowledge_base or "Foundry IQ"
            # Extractive output returns the grounded passages in `response` as a
            # JSON array of {ref_id, title, content/terms} objects.
            grounded_text = ""
            for msg in data.get("response", []):
                for c in msg.get("content", []):
                    if c.get("text"):
                        grounded_text += c["text"]
            results: list[dict[str, Any]] = []
            try:
                chunks = json.loads(grounded_text) if grounded_text else []
            except (ValueError, TypeError):
                chunks = []
            for chunk in (chunks if isinstance(chunks, list) else [])[:limit]:
                if not isinstance(chunk, dict):
                    continue
                body = chunk.get("content") or chunk.get("terms") or chunk.get("text") or ""
                if isinstance(body, (list, dict)):
                    body = json.dumps(body)
                results.append(
                    {
                        "title": chunk.get("title") or "Foundry IQ result",
                        "snippet": str(body)[:600],
                        "source": f"Foundry IQ · {kb_name}",
                    }
                )
            if not results and grounded_text:
                results.append(
                    {
                        "title": "Foundry IQ grounded answer",
                        "snippet": grounded_text[:600],
                        "source": f"Foundry IQ · {kb_name}",
                    }
                )
            results = results[:limit]
        except Exception as exc:  # pragma: no cover - defensive on preview API
            logger.warning("Foundry IQ retrieval failed, using fixtures: %s", exc)
            results = _match_fixtures(_WORK_IQ_FIXTURES, query, limit)
        _persist_lookup(source="foundry_iq", query=query, results=results)
        return results