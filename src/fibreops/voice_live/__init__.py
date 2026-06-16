"""Voice Live realtime helpers.

Builds the upstream Azure Voice Live WebSocket URL and headers and exposes a
small ``proxy_session`` coroutine that bridges a browser WebSocket to the
upstream service. The browser stays oblivious to the API key — it speaks the
realtime protocol directly with the service through this proxy.

Voice Live's realtime channel is OpenAI-Realtime-compatible; clients send
``session.update``, ``input_audio_buffer.append`` (base64 PCM16),
``conversation.item.create``, and ``response.create`` events, and receive
``response.audio.delta`` / ``response.text.delta`` events back.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional
from urllib.parse import urlencode, urlparse, urlunparse

from ..config import Settings, get_settings
from ..observability import get_logger

logger = get_logger(__name__)


def _to_ws_scheme(url: str) -> str:
    """Coerce http(s) → ws(s); leave ws(s) as-is."""
    p = urlparse(url)
    scheme = {"http": "ws", "https": "wss"}.get(p.scheme, p.scheme)
    return urlunparse(p._replace(scheme=scheme))


def build_upstream_url(settings: Optional[Settings] = None) -> Optional[str]:
    """Build the full upstream Voice Live WS URL (or None when not configured)."""
    settings = settings or get_settings()
    base = settings.azure_voice_live_endpoint
    if not base:
        return None
    base = _to_ws_scheme(base.rstrip("/"))
    parsed = urlparse(base)
    path = parsed.path or ""
    if "/voicelive/" not in path and "/realtime" not in path:
        path = "/voicelive/realtime"
    query_params: dict[str, str] = {"api-version": settings.azure_voice_live_api_version}
    if settings.azure_voice_live_agent_id:
        query_params["agent-id"] = settings.azure_voice_live_agent_id
    existing = parsed.query
    new_query = urlencode(query_params)
    query = f"{existing}&{new_query}" if existing else new_query
    return urlunparse(parsed._replace(path=path, query=query))


def build_upstream_headers(settings: Optional[Settings] = None) -> dict[str, str]:
    settings = settings or get_settings()
    headers: dict[str, str] = {}
    if settings.azure_voice_live_api_key:
        headers["api-key"] = settings.azure_voice_live_api_key
    return headers


def session_descriptor(settings: Optional[Settings] = None) -> dict[str, Any]:
    """Return the JSON descriptor the browser needs to open a session.

    The browser does not get the upstream URL or API key — it connects to
    ``ws_path`` on this server, which proxies to Voice Live.
    """
    settings = settings or get_settings()
    enabled = bool(settings.azure_voice_live_endpoint)
    return {
        "enabled": enabled,
        "ws_path": "/ws/voice" if enabled else None,
        "voice": settings.azure_voice_live_voice or "en-GB-LibbyNeural",
        "agent_id": settings.azure_voice_live_agent_id,
        "duplex_enabled": enabled and bool(settings.azure_voice_live_agent_id),
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
        try:
            upstream = await websockets.connect(  # type: ignore[attr-defined]
                upstream_url, additional_headers=headers, max_size=None
            )
        except TypeError:
            upstream = await websockets.connect(  # type: ignore[attr-defined]
                upstream_url, extra_headers=headers, max_size=None
            )
    except Exception as exc:  # pragma: no cover - depends on live service
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
        except Exception as exc:  # pragma: no cover - network conditions
            logger.debug("client→upstream pump ended: %s", exc)

    async def upstream_to_client() -> None:
        try:
            async for frame in upstream:
                if isinstance(frame, (bytes, bytearray)):
                    await client_ws.send_bytes(bytes(frame))
                else:
                    await client_ws.send_text(frame)
        except Exception as exc:  # pragma: no cover - network conditions
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
