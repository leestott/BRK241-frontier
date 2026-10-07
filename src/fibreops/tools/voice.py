"""Record deterministic incident announcements for the Foundry voice-agent UI."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..observability import tool_span
_OUTBOX = Path("state") / "voice_outbox.jsonl"

# Phrase templates keyed by the orchestrator step. Kept short and deliberate
# so they read well as TTS — the optimiser can mutate these later.
_PHRASES: dict[str, str] = {
    "outage_detected": (
        "A {severity} incident involving {signal_description} has been detected{location}. "
        "{impact} {cause}"
    ),
    "engineer_dispatched": (
        "{engineer} has been dispatched to incident {incident_id}. "
        "{arrival} The service restoration time has not been confirmed."
    ),
    "incident_resolved": (
        "Incident {incident_id}{location} has been resolved by "
        "{engineer}. Service is now restored."
    ),
}


def _voice_for_severity(severity: str) -> str:
    """Pick a voice that matches the urgency."""
    settings = get_settings()
    if settings.azure_voice_agent_voice:
        return settings.azure_voice_agent_voice
    if severity.lower() == "critical":
        return "en-GB-RyanNeural"
    return "en-GB-LibbyNeural"


def _build_ssml(*, voice: str, text: str, severity: str) -> str | None:
    """Export legacy Azure Neural SSML; native realtime voices use plain text."""
    if not voice.endswith("Neural"):
        return None
    rate = "+5%" if severity.lower() == "critical" else "0%"
    style = "newscast-formal" if severity.lower() == "critical" else "chat"
    return (
        '<speak version="1.0" xml:lang="en-GB" '
        'xmlns="http://www.w3.org/2001/10/synthesis" '
        'xmlns:mstts="https://www.w3.org/2001/mstts">'
        f'<voice name="{voice}">'
        f'<mstts:express-as style="{style}">'
        f'<prosody rate="{rate}">{text}</prosody>'
        '</mstts:express-as></voice></speak>'
    )


def _render_phrase(phrase_key: str, **fmt: Any) -> str:
    template = _PHRASES.get(phrase_key)
    if template is None:
        raise KeyError(f"Unknown voice phrase '{phrase_key}'. Add it to _PHRASES.")
    return template.format(**fmt)


def _record_announcement(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist the status text; the browser plays it through the voice agent."""
    _OUTBOX.parent.mkdir(exist_ok=True)
    with _OUTBOX.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")
    return {"status": "logged-locally", "outbox": str(_OUTBOX)}


def speak_status_update(
    *,
    incident_id: str,
    phrase: str = "outage_detected",
    severity: str = "medium",
    node_id: str | None = None,
    region: str | None = None,
    customers: int | None = None,
    probable_cause: str | None = None,
    engineer: str | None = None,
    eta: int | None = None,
    signal_type: str | None = None,
) -> dict[str, Any]:
    """Speak a one-line voice update for an incident.

    Returns a payload describing the utterance (always — webhook or outbox).
    The browser sends the recorded text to the voice agent when requested.
    """
    if customers is not None and customers < 0:
        raise ValueError("Customer count cannot be negative")
    if eta is not None and eta < 0:
        raise ValueError("Arrival ETA cannot be negative")
    signals = {
        "loss_of_light": "loss of optical signal",
        "node_unreachable": "an unreachable node",
        "high_attenuation": "high signal attenuation",
        "ber_degradation": "an increased bit-error rate",
    }
    if signal_type is not None and signal_type not in signals:
        raise ValueError(f"Unknown incident signal type: {signal_type}")
    with tool_span(
        "voice.speak_status_update", incident_id=incident_id, phrase=phrase, severity=severity
    ):
        location = f" on node {node_id}" if node_id else ""
        if region:
            location += f" in {region}"
        text = _render_phrase(
            phrase,
            severity=severity,
            node_id=node_id,
            region=region,
            location=location,
            signal_description=signals.get(signal_type, "a network fault"),
            impact=(
                f"The incident may affect {customers:,} customers."
                if customers is not None else "The number of affected customers is not yet known."
            ),
            cause=(
                f"The suspected cause is {probable_cause}; it has not been confirmed."
                if probable_cause else "The cause has not been confirmed."
            ),
            engineer=engineer or "A technician",
            arrival=(
                f"The estimated technician arrival time is {eta} minutes."
                if eta is not None else "The technician arrival time is not yet available."
            ),
            incident_id=incident_id,
        )
        voice = _voice_for_severity(severity)
        ssml = _build_ssml(voice=voice, text=text, severity=severity)
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "incident_id": incident_id,
            "phrase": phrase,
            "voice": voice,
            "text": text,
            "ssml": ssml,
            "severity": severity,
            "facts": {
                "incident_id": incident_id, "node_id": node_id, "region": region,
                "signal_type": signal_type,
                "severity": severity, "potentially_affected_customers": customers,
                "customer_count_is_approximate": False,
                "suspected_cause": probable_cause, "cause_confirmed": False,
                "dispatch_status": "dispatched" if phrase == "engineer_dispatched" else "unknown",
                "engineer": engineer, "technician_arrival_eta_minutes": eta,
                "service_restoration_eta_minutes": None,
                "restoration_confirmed": phrase == "incident_resolved",
            },
        }
        payload["delivery"] = _record_announcement(payload)
        return payload
