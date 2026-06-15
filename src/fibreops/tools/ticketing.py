"""D365 Field Service ticketing tool (Dataverse-shaped REST).

Talks to the mock D365 service by default; point D365_MOCK_BASE_URL at a real
Dataverse Web API endpoint to switch over. Payloads/endpoints follow the
v9.2 path convention used by Dataverse.
"""
from __future__ import annotations

from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import get_settings
from ..observability import get_logger, tool_span

logger = get_logger(__name__)


def _base_url() -> str:
    return get_settings().d365_mock_base_url.rstrip("/")


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=0.4, min=0.5, max=5))
def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    with httpx.Client(timeout=8.0) as client:
        r = client.post(f"{_base_url()}{path}", json=payload)
        r.raise_for_status()
        return r.json()


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=0.4, min=0.5, max=5))
def _patch(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    with httpx.Client(timeout=8.0) as client:
        r = client.patch(f"{_base_url()}{path}", json=payload)
        r.raise_for_status()
        return r.json()


def create_ticket(
    *,
    incident_id: str,
    node_id: str,
    severity: str,
    title: str,
    description: str,
) -> dict[str, Any]:
    """Create a Field Service ticket from an incident."""
    with tool_span("ticketing.create_ticket", incident_id=incident_id, severity=severity):
        payload = {
            "incident_id": incident_id,
            "node_id": node_id,
            "severity": severity,
            "title": title,
            "description": description,
        }
        result = _post("/api/data/v9.2/incidents", payload)
        logger.info("D365 ticket created: %s", result.get("ticket_id"))
        return result


def update_ticket(
    *,
    ticket_id: str,
    status: str | None = None,
    assignee: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Patch a ticket status / assignee."""
    with tool_span("ticketing.update_ticket", ticket_id=ticket_id):
        payload = {k: v for k, v in {"status": status, "assignee": assignee, "notes": notes}.items() if v is not None}
        return _patch(f"/api/data/v9.2/incidents/{ticket_id}", payload)


def create_booking(
    *,
    incident_id: str,
    engineer_id: str,
    engineer_name: str,
    eta_minutes: int,
    notes: str = "",
) -> dict[str, Any]:
    """Create the bookable resource booking that represents an engineer dispatch."""
    with tool_span("ticketing.create_booking", incident_id=incident_id, engineer_id=engineer_id):
        return _post(
            "/api/data/v9.2/bookableresourcebookings",
            {
                "incident_id": incident_id,
                "engineer_id": engineer_id,
                "engineer_name": engineer_name,
                "eta_minutes": eta_minutes,
                "notes": notes,
            },
        )
