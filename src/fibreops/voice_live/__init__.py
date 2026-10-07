"""Server-side bridge to a Microsoft Foundry voice agent (preview)."""
from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import quote, urlencode, urlparse

from azure.identity.aio import DefaultAzureCredential

from ..config import Settings, get_settings
from ..observability import get_logger
from .agent_tools import dispatch as dispatch_tool

logger = get_logger(__name__)
_SCOPE = "https://ai.azure.com/.default"
_FEATURE = "VoiceAgents=V1Preview"
_ALLOWED_EVENTS = {
    "conversation.item.create",
    "response.create",
    "response.cancel",
    "input_audio_buffer.append",
    "input_audio_buffer.commit",
    "input_audio_buffer.clear",
}


def build_upstream_url(settings: Settings | None = None) -> str | None:
    settings = settings or get_settings()
    endpoint = settings.azure_ai_project_endpoint
    name = settings.azure_voice_agent_name
    if not endpoint or not name:
        return None
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.netloc or not parsed.path.startswith("/api/projects/"):
        raise ValueError("AZURE_AI_PROJECT_ENDPOINT must be a Foundry HTTPS project endpoint")
    query = {"api-version": "v1"}
    if settings.azure_voice_agent_version:
        query["x-agent-version-override"] = settings.azure_voice_agent_version
    return (
        f"wss://{parsed.netloc}{parsed.path.rstrip('/')}"
        f"/agents/{quote(name, safe='')}/endpoint/protocols/voice?{urlencode(query)}"
    )


def session_descriptor(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    enabled = bool(settings.azure_ai_project_endpoint and settings.azure_voice_agent_name)
    return {
        "enabled": enabled,
        "ws_path": "/ws/voice" if enabled else None,
        "duplex_enabled": enabled,
        "agent_name": settings.azure_voice_agent_name if enabled else None,
    }


async def proxy_session(client_ws: Any) -> None:
    """Relay realtime events without exposing the Foundry bearer token to the browser."""
    url = build_upstream_url()
    if not url:
        await client_ws.close(code=1011, reason="Voice agent not configured")
        return

    import websockets

    async with DefaultAzureCredential() as credential:
        token = (await credential.get_token(_SCOPE)).token
    try:
        upstream = await websockets.connect(
            url,
            additional_headers={
                "Authorization": f"Bearer {token}",
                "Foundry-Features": _FEATURE,
            },
            max_size=None,
            ping_interval=20,
        )
    except Exception:
        logger.exception("Foundry voice agent connection failed")
        await client_ws.close(code=1011, reason="Voice agent connection failed")
        return

    cancelled_responses: set[str] = set()

    async def client_to_upstream() -> None:
        while True:
            message = await client_ws.receive()
            if message["type"] == "websocket.disconnect":
                return
            text = message.get("text")
            if text is None:
                await client_ws.close(code=1003, reason="Expected realtime JSON")
                return
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                await client_ws.close(code=1003, reason="Invalid realtime JSON")
                return
            if not isinstance(event, dict) or event.get("type") not in _ALLOWED_EVENTS:
                await client_ws.close(code=1008, reason="Unsupported realtime event")
                return
            if event["type"] == "conversation.item.create":
                item = event.get("item")
                if not isinstance(item, dict) or item.get("role") != "user":
                    await client_ws.close(code=1008, reason="Only user messages are allowed")
                    return
            if event["type"] == "response.cancel" and isinstance(event.get("response_id"), str):
                cancelled_responses.add(event["response_id"])
            await upstream.send(text)

    async def upstream_to_client() -> None:
        ready_outputs: list[tuple[str, str]] = []
        async for frame in upstream:
            if isinstance(frame, bytes):
                await client_ws.send_bytes(frame)
                continue
            await client_ws.send_text(frame)
            try:
                event = json.loads(frame)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "response.function_call_arguments.done":
                call_id = event.get("call_id")
                name = event.get("name")
                if call_id and name:
                    try:
                        result = await dispatch_tool(name, event.get("arguments", "{}"))
                    except Exception:
                        logger.exception("Voice agent tool %s failed", name)
                        result = json.dumps({"error": "Tool failed; check server logs."})
                    ready_outputs.append((call_id, result))
            elif event.get("type") == "response.done":
                response = event.get("response", {})
                response_id = response.get("id")
                cancelled = response.get("status") == "cancelled" or response_id in cancelled_responses
                cancelled_responses.discard(response_id)
                if ready_outputs:
                    for call_id, output in ready_outputs:
                        await upstream.send(json.dumps({
                            "type": "conversation.item.create",
                            "item": {
                                "type": "function_call_output",
                                "call_id": call_id,
                                "output": output,
                            },
                        }))
                    ready_outputs.clear()
                    if not cancelled:
                        await upstream.send(json.dumps({"type": "response.create"}))

    tasks = [
        asyncio.create_task(client_to_upstream()),
        asyncio.create_task(upstream_to_client()),
    ]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    finally:
        await upstream.close()
        await client_ws.close()
