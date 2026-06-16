"""Voice Live realtime helpers.

Builds the upstream Azure Voice Live WebSocket URL and exposes a
``proxy_session`` coroutine that bridges a browser WebSocket to the
upstream service. The browser stays oblivious to credentials — it speaks
the realtime protocol directly with the service through this proxy.

Auth (matching microsoft/foundry-agent-voice-mode-sample):
  - Entra bearer token via DefaultAzureCredential (preferred in Azure)
  - Falls back to api-key header for local dev when no managed identity
  - Agent mode: token goes in URL as ``authorization=Bearer+<token>``
  - Non-agent mode: token in ``Authorization`` header
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Optional
from urllib.parse import quote, urlparse, urlunparse

from ..config import Settings, get_settings
from ..observability import get_logger

logger = get_logger(__name__)

# Entra scopes Voice Live accepts (try new scope first)
_PRIMARY_SCOPE = "https://ai.azure.com/.default"
_LEGACY_SCOPE = "https://cognitiveservices.azure.com/.default"


def _to_wss_host(url: str) -> str:
    """Extract bare hostname from any URL scheme; remap .services.ai.azure.com."""
    p = urlparse(url.strip().rstrip("/"))
    host = p.netloc or p.path.split("/", 1)[0]
    if ".services.ai.azure.com" in host:
        host = host.replace(".services.ai.azure.com", ".cognitiveservices.azure.com")
    return host


def _get_bearer_token() -> Optional[str]:
    """Return Entra access token via managed identity / DefaultAzureCredential."""
    try:
        from azure.identity import DefaultAzureCredential  # type: ignore[import-not-found]
        cred = DefaultAzureCredential()
        try:
            return cred.get_token(_PRIMARY_SCOPE).token
        except Exception:
            return cred.get_token(_LEGACY_SCOPE).token
    except Exception as exc:
        logger.debug("Could not acquire bearer token: %s", exc)
        return None


def build_upstream_url(settings: Optional[Settings] = None) -> Optional[str]:
    """Build the upstream Voice Live WS URL with embedded auth (agent mode)
    or plain URL (non-agent mode where auth goes in headers).

    Agent mode matches the Foundry Portal URL shape from the reference sample:
      wss://<host>/voice-live/realtime
        ?trafficType=FoundryPortal
        &agent-name=<id>&agent-version=&agent-project-name=<project>
        &api-version=<ver>&model=<id>&client-request-id=<crid>
        &authorization=Bearer+<token>
    """
    settings = settings or get_settings()
    base = settings.azure_voice_live_endpoint
    if not base:
        return None

    host = _to_wss_host(base)
    api_version = settings.azure_voice_live_api_version
    agent_id = settings.azure_voice_live_agent_id

    if agent_id:
        # Agent mode — bearer token embedded in URL (reference pattern)
        token = _get_bearer_token()
        if not token:
            logger.error("Voice Live agent mode requires Entra auth; no token available")
            return None
        project_name = getattr(settings, "azure_ai_project_name", None) or ""
        crid = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:10]}"
        qs = (
            f"trafficType=FoundryPortal"
            f"&agent-name={quote(agent_id, safe='')}"
            f"&agent-version="
            f"&agent-project-name={quote(project_name, safe='')}"
            f"&api-version={quote(api_version, safe='')}"
            f"&model={quote(agent_id, safe='')}"
            f"&client-request-id={crid}"
            f"&authorization=Bearer+{quote(token, safe='')}"
        )
        return f"wss://{host}/voice-live/realtime?{qs}"
    else:
        # Non-agent / direct model mode — plain URL, auth in headers
        model = getattr(settings, "azure_ai_model_deployment", None) or "gpt-4.1-mini"
        return f"wss://{host}/voice-live/realtime?api-version={quote(api_version, safe='')}&model={quote(model, safe='')}"


def build_upstream_headers(settings: Optional[Settings] = None) -> dict[str, str]:
    """Return auth headers for non-agent mode.

    Preference order:
      1. Entra bearer token (Authorization: Bearer …)
      2. api-key header fallback for local dev
    """
    settings = settings or get_settings()
    if settings.azure_voice_live_agent_id:
        # Agent mode: auth is already in the URL, no extra headers
        return {}

    token = _get_bearer_token()
    if token:
        return {"Authorization": f"Bearer {token}"}

    if settings.azure_voice_live_api_key:
        logger.debug("Voice Live: falling back to api-key header auth")
        return {"api-key": settings.azure_voice_live_api_key}

    logger.warning("Voice Live: no auth available (no managed identity, no api-key)")
    return {}


def session_descriptor(settings: Optional[Settings] = None) -> dict[str, Any]:
    """Return the JSON descriptor the browser needs to open a session."""
    settings = settings or get_settings()
    enabled = bool(settings.azure_voice_live_endpoint)
    voice_name = settings.azure_voice_live_voice or "en-GB-RyanNeural"
    return {
        "enabled": enabled,
        "ws_path": "/ws/voice" if enabled else None,
        "voice": voice_name,
        "voice_type": "azure-standard",
        "agent_id": settings.azure_voice_live_agent_id,
        "duplex_enabled": enabled,
    }


async def proxy_session(client_ws: Any) -> None:
    """Bridge a FastAPI WebSocket to the upstream Voice Live WebSocket."""
    upstream_url = build_upstream_url()
    if not upstream_url:
        await client_ws.close(code=1011, reason="Voice Live not configured")
        return
    try:
        import websockets  # type: ignore[import-not-found]
    except ImportError:
        logger.error("websockets package not installed; cannot proxy Voice Live")
        await client_ws.close(code=1011, reason="websockets dependency missing")
        return

    headers = build_upstream_headers()
    logger.info("Opening upstream Voice Live WS: %s", upstream_url.split("?", 1)[0])
    try:
        # websockets >=13 uses additional_headers; older versions used extra_headers
        try:
            upstream = await websockets.connect(  # type: ignore[attr-defined]
                upstream_url, additional_headers=headers, max_size=None, ping_interval=20
            )
        except TypeError:
            upstream = await websockets.connect(  # type: ignore[attr-defined]
                upstream_url, extra_headers=headers, max_size=None, ping_interval=20
            )
    except Exception as exc:
        logger.warning("Voice Live upstream connect failed: %s", exc)
        await client_ws.close(code=1011, reason=f"upstream connect failed: {exc}")
        return

    async def client_to_upstream() -> None:
        try:
            while True:
                message = await client_ws.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                if (text := message.get("text")) is not None:
                    await upstream.send(text)
                elif (data := message.get("bytes")) is not None:
                    await upstream.send(data)
        except Exception as exc:
            logger.debug("client→upstream pump ended: %s", exc)

    async def upstream_to_client() -> None:
        try:
            async for frame in upstream:
                if isinstance(frame, (bytes, bytearray)):
                    await client_ws.send_bytes(bytes(frame))
                else:
                    await client_ws.send_text(frame)
        except Exception as exc:
            logger.debug("upstream→client pump ended: %s", exc)

    try:
        await asyncio.gather(
            client_to_upstream(), upstream_to_client(), return_exceptions=True
        )
    finally:
        try:
            await upstream.close()
        except Exception:
            pass
        try:
            await client_ws.close()
        except Exception:
            pass

