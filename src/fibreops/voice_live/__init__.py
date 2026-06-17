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
import json
import time
import uuid
from typing import Any, Optional
from urllib.parse import quote, urlparse, urlunparse

from ..config import Settings, get_settings
from ..observability import get_logger
from .agent_tools import INSTRUCTIONS, TOOL_DEFINITIONS, dispatch as dispatch_tool

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
        if not project_name and settings.azure_ai_project_endpoint:
            # Derive from endpoint path: …/api/projects/<project_name>
            tail = urlparse(settings.azure_ai_project_endpoint).path.rstrip("/").rsplit("/", 1)
            if len(tail) == 2:
                project_name = tail[1]
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


def _augment_session_update(text: str) -> tuple[str, bool]:
    """If *text* is a session.update event, merge FibreOps instructions+tools.

    Only augments when the session uses turn detection (mic/duplex mode).
    One-shot TTS sessions (speak mode, ``turn_detection: null``) are left
    untouched so the model just renders the engineer's text without trying
    to call lookup tools on it.

    Returns ``(new_text, was_session_update)``. When the input is not a
    session.update event (or not JSON at all), returns the original text
    untouched and ``was_session_update=False``.
    """
    try:
        evt = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text, False
    if not isinstance(evt, dict) or evt.get("type") != "session.update":
        return text, False
    sess = evt.setdefault("session", {})
    if not isinstance(sess, dict):
        return text, True
    # Skip augmentation in one-shot speak mode — only mic/duplex sessions
    # benefit from tool calling.
    if not sess.get("turn_detection"):
        return text, True
    if not sess.get("instructions"):
        sess["instructions"] = INSTRUCTIONS
    sess["tools"] = TOOL_DEFINITIONS
    sess["tool_choice"] = sess.get("tool_choice") or "auto"
    return json.dumps(evt), True


async def _handle_function_call(upstream: Any, call_id: str, name: str, args_json: str) -> None:
    """Deprecated. Inline-flushed in ``upstream_to_client`` after response.done."""
    raise NotImplementedError


async def proxy_session(client_ws: Any) -> None:
    """Bridge a FastAPI WebSocket to the upstream Voice Live WebSocket."""
    settings = get_settings()
    upstream_url = build_upstream_url(settings)
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

    # Server-side tool augmentation for non-agent mode. When we don't have a
    # Voice Live agent bound, inject FibreOps instructions + function tools into
    # the first session.update the client sends so the realtime model can call
    # back into our Python helpers (lookup incidents, nodes, SOPs, etc.).
    augment_tools = not settings.azure_voice_live_agent_id
    session_augmented = {"done": False}

    async def client_to_upstream() -> None:
        try:
            while True:
                message = await client_ws.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                text = message.get("text")
                data = message.get("bytes")
                if text is not None:
                    if augment_tools and not session_augmented["done"]:
                        new_text, was_session_update = _augment_session_update(text)
                        if was_session_update:
                            session_augmented["done"] = True
                        text = new_text
                    await upstream.send(text)
                elif data is not None:
                    await upstream.send(data)
        except Exception as exc:
            logger.debug("client→upstream pump ended: %s", exc)

    async def upstream_to_client() -> None:
        # Track in-flight function calls and outputs to flush only after the
        # current response has fully completed. The OpenAI realtime contract
        # rejects ``response.create`` while a response is still active, so we
        # buffer ``function_call_output`` items until ``response.done`` arrives.
        pending_calls: dict[str, dict[str, str]] = {}
        ready_outputs: list[tuple[str, str]] = []  # (call_id, output_json)
        try:
            async for frame in upstream:
                if isinstance(frame, (bytes, bytearray)):
                    await client_ws.send_bytes(bytes(frame))
                    continue
                # Always forward the raw frame to the browser first so it sees
                # the same event stream the model produced.
                await client_ws.send_text(frame)
                if not augment_tools:
                    continue
                try:
                    evt = json.loads(frame)
                except (json.JSONDecodeError, TypeError):
                    continue
                etype = evt.get("type")
                if etype == "response.output_item.added":
                    item = evt.get("item") or {}
                    if item.get("type") == "function_call":
                        call_id = item.get("call_id") or item.get("id")
                        name = item.get("name", "")
                        if call_id:
                            pending_calls[call_id] = {"name": name, "args": ""}
                elif etype == "response.function_call_arguments.delta":
                    call_id = evt.get("call_id")
                    delta = evt.get("delta", "")
                    if call_id and call_id in pending_calls:
                        pending_calls[call_id]["args"] += delta
                elif etype == "response.function_call_arguments.done":
                    call_id = evt.get("call_id")
                    name = evt.get("name") or pending_calls.get(call_id, {}).get("name", "")
                    args_json = evt.get("arguments") or pending_calls.get(call_id, {}).get("args", "")
                    pending_calls.pop(call_id, None)
                    if not call_id or not name:
                        continue
                    logger.info("Voice tool call: %s args=%s", name, args_json[:200])
                    try:
                        output = await dispatch_tool(name, args_json)
                    except Exception as exc:
                        logger.warning("dispatch_tool crashed: %s", exc)
                        output = json.dumps({"error": str(exc)})
                    ready_outputs.append((call_id, output))
                elif etype == "response.done" and ready_outputs:
                    # Flush all buffered tool outputs, then ask the model to
                    # continue with a single response.create.
                    for call_id, output in ready_outputs:
                        try:
                            await upstream.send(json.dumps({
                                "type": "conversation.item.create",
                                "item": {
                                    "type": "function_call_output",
                                    "call_id": call_id,
                                    "output": output,
                                },
                            }))
                        except Exception as exc:
                            logger.warning("Failed to send function_call_output: %s", exc)
                    ready_outputs.clear()
                    try:
                        await upstream.send(json.dumps({"type": "response.create"}))
                    except Exception as exc:
                        logger.warning("Failed to send response.create: %s", exc)
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

