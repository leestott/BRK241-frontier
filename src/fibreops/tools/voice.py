"""Azure AI Voice Live integration (mock-by-default).

The BRK241 deck announces **Voice Live integration with Foundry Agent Service**
on slide 8 and shows an "Interact with Voice" arrow into the agent system on
slide 4. Voice Live is a real-time speech-in/speech-out gateway in front of a
Foundry agent (typically reached over WebSocket).

For a stage demo we don't need to spin up a microphone; we just need to prove
the agent emits voice utterances at the right moments. This module:

1. Builds an SSML utterance for an incident status update.
2. POSTs it to ``AZURE_VOICE_LIVE_ENDPOINT`` when configured (treated as a
   webhook-style endpoint that accepts JSON ``{voice, ssml}`` — the real Voice
   Live channel uses a WebSocket but the wire format for a one-shot synthesis
   call is identical, so the same payload swaps cleanly).
3. Otherwise appends the utterance to ``state/voice_outbox.jsonl`` so the UI
   can display it and tests can assert on it.

This keeps the demo deterministic on stage while leaving a single env-var
swap (``AZURE_VOICE_LIVE_ENDPOINT``) to light up the real Voice Live channel.
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
_OUTBOX = Path("state") / "voice_outbox.jsonl"

# Phrase templates keyed by the orchestrator step. Kept short and deliberate
# so they read well as TTS — the optimiser can mutate these later.
_PHRASES: dict[str, str] = {
    "outage_detected": (
        "Heads up team — a {severity} fibre outage has been detected on node "
        "{node_id} in {region}. Approximately {customers} customers are "
        "potentially affected. Probable cause: {probable_cause}."
    ),
    "engineer_dispatched": (
        "Engineer {engineer} has been dispatched to incident {incident_id}. "
        "Estimated time of arrival is {eta} minutes."
    ),
    "incident_resolved": (
        "Incident {incident_id} on node {node_id} has been resolved by "
        "{engineer}. Service is now restored."
    ),
}


def _voice_for_severity(severity: str) -> str:
    """Pick a voice that matches the urgency."""
    settings = get_settings()
    if settings.azure_voice_live_voice:
        return settings.azure_voice_live_voice
    if severity.lower() == "critical":
        return "en-GB-RyanNeural"
    return "en-GB-LibbyNeural"


def _build_ssml(*, voice: str, text: str, severity: str) -> str:
    """Build an SSML body. Critical incidents speak with mild emphasis."""
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
    safe = {k: ("?" if v is None else v) for k, v in fmt.items()}
    return template.format(**safe)


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=0.5, min=0.5, max=4))
def _post_voice_live(payload: dict[str, Any]) -> dict[str, Any]:
    """POST the SSML to Azure Voice Live (or fall back to the local outbox).

    Real Voice Live runs over WebSocket; for a demo-friendly synthesis call
    we treat the configured endpoint as an HTTPS POST that accepts the same
    JSON payload. This lets a customer wire a Voice Live front door (or a
    plain Azure AI Speech TTS endpoint) with zero code changes.
    """
    settings = get_settings()
    if not settings.azure_voice_live_endpoint:
        _OUTBOX.parent.mkdir(exist_ok=True)
        with _OUTBOX.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")
        logger.info("Voice Live not configured; appended utterance to outbox")
        return {"status": "logged-locally", "outbox": str(_OUTBOX)}
    headers = {"Content-Type": "application/json"}
    if settings.azure_voice_live_api_key:
        headers["Ocp-Apim-Subscription-Key"] = settings.azure_voice_live_api_key
    with httpx.Client(timeout=10.0) as client:
        response = client.post(
            settings.azure_voice_live_endpoint, json=payload, headers=headers
        )
        response.raise_for_status()
        return {"status": "sent", "http_status": response.status_code}


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
) -> dict[str, Any]:
    """Speak a one-line voice update for an incident.

    Returns a payload describing the utterance (always — webhook or outbox).
    Safe to call from agents: never raises if the local outbox is reachable.
    """
    with tool_span(
        "voice.speak_status_update", incident_id=incident_id, phrase=phrase, severity=severity
    ):
        text = _render_phrase(
            phrase,
            severity=severity,
            node_id=node_id,
            region=region,
            customers=f"{customers:,}" if customers is not None else "?",
            probable_cause=probable_cause,
            engineer=engineer,
            eta=eta,
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
        }
        try:
            result = _post_voice_live(payload)
        except Exception as exc:  # pragma: no cover - defensive on flaky webhooks
            logger.warning("Voice Live POST failed, falling back to outbox: %s", exc)
            _OUTBOX.parent.mkdir(exist_ok=True)
            with _OUTBOX.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload) + "\n")
            result = {"status": "logged-locally-after-failure"}
        payload["delivery"] = result
        return payload
