"""Microsoft Teams integration.

Uses a Power Automate / Incoming Webhook URL with an Adaptive Card payload so
the message renders nicely in the channel. Falls back to logging the payload
to ./state/teams_outbox.jsonl if the webhook is not configured.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import get_settings
from ..observability import get_logger, tool_span

logger = get_logger(__name__)
_OUTBOX = Path("state") / "teams_outbox.jsonl"


def _adaptive_card(title: str, body_facts: list[tuple[str, str]], severity: str, summary: str) -> dict[str, Any]:
    color = {
        "critical": "attention",
        "high": "warning",
        "medium": "accent",
        "low": "good",
    }.get(severity.lower(), "default")
    facts = [{"title": k, "value": v} for k, v in body_facts]
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {"type": "TextBlock", "size": "Large", "weight": "Bolder", "text": title, "color": color},
                        {"type": "TextBlock", "text": summary, "wrap": True},
                        {"type": "FactSet", "facts": facts},
                        {
                            "type": "TextBlock",
                            "spacing": "Small",
                            "isSubtle": True,
                            "text": f"Posted by FibreOps Agent System at {datetime.now(timezone.utc).isoformat()}",
                        },
                    ],
                },
            }
        ],
    }


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, min=0.5, max=4))
def _post_webhook(card: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    if not settings.teams_enabled:
        _OUTBOX.parent.mkdir(exist_ok=True)
        with _OUTBOX.open("a", encoding="utf-8") as f:
            f.write(json.dumps(card) + "\n")
        logger.info("Teams webhook not configured; appended to outbox")
        return {"status": "logged-locally"}
    with httpx.Client(timeout=10.0) as client:
        response = client.post(settings.teams_webhook_url, json=card)
        response.raise_for_status()
        return {"status": "sent", "http_status": response.status_code}


def post_outage_notice(
    *,
    incident_id: str,
    node_id: str,
    severity: str,
    summary: str,
    customer_impact: str,
    probable_cause: str,
) -> dict[str, Any]:
    """Publish an initial outage notice to Microsoft Teams."""
    with tool_span("teams.post_outage_notice", incident_id=incident_id, severity=severity):
        card = _adaptive_card(
            title=f"🚨 Fibre outage detected — {severity.upper()}",
            severity=severity,
            summary=summary,
            body_facts=[
                ("Incident", incident_id),
                ("Node", node_id),
                ("Probable cause", probable_cause),
                ("Customer impact", customer_impact),
            ],
        )
        return _post_webhook(card)


def post_status_update(
    *,
    incident_id: str,
    status: str,
    note: str,
    engineer_name: str | None = None,
    eta_minutes: int | None = None,
) -> dict[str, Any]:
    """Publish a status update (assignment, ETA, resolution) to Teams."""
    with tool_span("teams.post_status_update", incident_id=incident_id, status=status):
        facts = [("Incident", incident_id), ("Status", status)]
        if engineer_name:
            facts.append(("Engineer", engineer_name))
        if eta_minutes is not None:
            facts.append(("ETA", f"{eta_minutes} min"))
        card = _adaptive_card(
            title=f"🔧 Incident {incident_id} update — {status}",
            severity="medium",
            summary=note,
            body_facts=facts,
        )
        return _post_webhook(card)
