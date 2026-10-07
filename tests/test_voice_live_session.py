"""Tests for Foundry Voice Agents Preview session/proxy plumbing.

The realtime upstream is not contacted in tests.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import fibreops.ui.app as ui_module
from fibreops import config
from fibreops.voice_live import (
    build_upstream_url,
    proxy_session,
    session_descriptor,
)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, chdir_state_tmp: Path) -> TestClient:
    monkeypatch.setenv("FIBREOPS_UI_SKIP_MOCK_D365", "1")
    return TestClient(ui_module.app)


def _reload_settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    config.get_settings.cache_clear()


def test_voice_definition_defaults_to_english_with_explicit_per_turn_overrides() -> None:
    definition_path = (
        Path(__file__).parents[1] / "src" / "fibreops" / "voice_live" / "definition.json"
    )
    definition = json.loads(definition_path.read_text(encoding="utf-8"))
    instructions = definition["instructions"]

    assert "APPLY INDEPENDENTLY ON EVERY USER TURN" in instructions
    assert "An earlier language override expires at the next user turn" in instructions
    assert "ENGLISH IS THE DEFAULT RESPONSE LANGUAGE" in instructions
    assert "Polish question without a language request -> English" in instructions
    assert "Arabic question without a language request -> English" in instructions
    assert "'answer in Polish' -> Polish for that turn" in instructions
    assert "'answer in Arabic' -> Arabic for that turn" in instructions
    assert "Re-check this policy after tool calls" in instructions
    assert "uncertainty, negation, quantities, units, incident IDs" in instructions
    assert "sieć światłowodowa" in instructions
    assert "Modern Standard Arabic" in instructions
    assert "Exact quantities must stay exact" in instructions
    assert "Awaria może dotyczyć" in instructions


def test_voice_agent_defaults_to_native_multilingual_voice() -> None:
    field = config.Settings.model_fields["azure_voice_agent_voice"]
    assert field.default == "marin"


def test_build_upstream_url_unconfigured() -> None:
    config.get_settings.cache_clear()
    assert build_upstream_url(config.Settings(AZURE_VOICE_AGENT_NAME="")) is None


def test_build_upstream_url_uses_project_and_voice_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    _reload_settings(
        monkeypatch,
        AZURE_AI_PROJECT_ENDPOINT="https://foundry.services.ai.azure.com/api/projects/project",
        AZURE_VOICE_AGENT_NAME="noc voice",
        AZURE_VOICE_AGENT_VERSION="2",
    )
    url = build_upstream_url()
    assert url == (
        "wss://foundry.services.ai.azure.com/api/projects/project/agents/"
        "noc%20voice/endpoint/protocols/voice?api-version=v1&x-agent-version-override=2"
    )
    assert "Bearer" not in url


def test_build_upstream_url_rejects_non_project_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _reload_settings(
        monkeypatch,
        AZURE_AI_PROJECT_ENDPOINT="https://example.com/voice-live/realtime",
        AZURE_VOICE_AGENT_NAME="noc",
    )
    with pytest.raises(ValueError, match="Foundry HTTPS project endpoint"):
        build_upstream_url()


def test_proxy_uses_preview_header_and_rejects_session_override(monkeypatch: pytest.MonkeyPatch) -> None:
    _reload_settings(
        monkeypatch,
        AZURE_AI_PROJECT_ENDPOINT="https://foundry.services.ai.azure.com/api/projects/project",
        AZURE_VOICE_AGENT_NAME="fibreops-noc-voice",
    )
    captured: dict[str, object] = {}

    class Credential:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get_token(self, scope):
            assert scope == "https://ai.azure.com/.default"
            return SimpleNamespace(token="test-token")

    class Upstream:
        sent: list[str] = []

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(60)
            raise StopAsyncIteration

        async def send(self, text):
            self.sent.append(text)

        async def close(self):
            return None

    class Client:
        closed: list[int] = []

        async def receive(self):
            return {"type": "websocket.receive", "text": json.dumps({
                "type": "session.update", "session": {"instructions": "override"}
            })}

        async def close(self, code=1000, reason=""):
            self.closed.append(code)

    async def connect(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs["additional_headers"]
        return Upstream()

    monkeypatch.setattr("fibreops.voice_live.DefaultAzureCredential", Credential)
    monkeypatch.setattr("websockets.connect", connect)
    client_ws = Client()
    asyncio.run(proxy_session(client_ws))

    assert "test-token" not in captured["url"]
    assert captured["headers"] == {
        "Authorization": "Bearer test-token",
        "Foundry-Features": "VoiceAgents=V1Preview",
    }
    assert 1008 in client_ws.closed


def test_session_descriptor_disabled_by_default() -> None:
    desc = session_descriptor(config.Settings(AZURE_VOICE_AGENT_NAME=""))
    assert desc["enabled"] is False
    assert desc["ws_path"] is None
    assert desc["duplex_enabled"] is False


def test_stop_during_tool_does_not_restart_speech(monkeypatch: pytest.MonkeyPatch) -> None:
    _reload_settings(
        monkeypatch,
        AZURE_AI_PROJECT_ENDPOINT="https://foundry.services.ai.azure.com/api/projects/project",
        AZURE_VOICE_AGENT_NAME="noc",
    )
    sent = []

    async def run():
        tool_started, cancel_sent = asyncio.Event(), asyncio.Event()

        class Credential:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            async def get_token(self, scope):
                return SimpleNamespace(token="test-token")

        class Upstream:
            async def send(self, text):
                event = json.loads(text)
                sent.append(event)
                if event["type"] == "response.cancel":
                    cancel_sent.set()

            async def close(self):
                pass

            def __aiter__(self):
                return self.events()

            async def events(self):
                yield json.dumps({
                    "type": "response.function_call_arguments.done",
                    "call_id": "call", "name": "lookup_node", "arguments": "{}",
                })
                yield json.dumps({
                    "type": "response.done", "response": {"id": "r1", "status": "completed"},
                })

        class Client:
            first = True

            async def receive(self):
                if self.first:
                    self.first = False
                    await tool_started.wait()
                    return {"type": "websocket.receive", "text": json.dumps({
                        "type": "response.cancel", "response_id": "r1",
                    })}
                await asyncio.sleep(60)

            async def send_text(self, text):
                pass

            async def close(self, **kwargs):
                pass

        async def connect(*args, **kwargs):
            return Upstream()

        async def dispatch(*args):
            tool_started.set()
            await cancel_sent.wait()
            return "{}"

        monkeypatch.setattr("fibreops.voice_live.DefaultAzureCredential", Credential)
        monkeypatch.setattr("fibreops.voice_live.dispatch_tool", dispatch)
        monkeypatch.setattr("websockets.connect", connect)
        await asyncio.wait_for(proxy_session(Client()), timeout=5)

    asyncio.run(run())
    assert any(event["type"] == "conversation.item.create" for event in sent)
    assert not any(event["type"] == "response.create" for event in sent)


def test_session_descriptor_enabled_for_published_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    _reload_settings(
        monkeypatch,
        AZURE_AI_PROJECT_ENDPOINT="https://foundry.services.ai.azure.com/api/projects/project",
        AZURE_VOICE_AGENT_NAME="fibreops-noc-voice",
    )
    desc = session_descriptor()
    assert desc["enabled"] is True
    assert desc["ws_path"] == "/ws/voice"
    assert desc["duplex_enabled"] is True
    assert desc["agent_name"] == "fibreops-noc-voice"
    assert "api_key" not in desc


def test_api_voice_session_endpoint(client: TestClient) -> None:
    config.get_settings.cache_clear()
    r = client.get("/api/voice/session")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) >= {"enabled", "ws_path", "agent_name", "duplex_enabled"}


def test_ws_voice_closes_when_unconfigured(client: TestClient) -> None:
    """Without a published voice agent the proxy must refuse cleanly."""
    ui_module.get_settings.cache_clear()
    from fibreops.voice_live import session_descriptor

    if session_descriptor()["enabled"]:
        pytest.skip("Locally configured voice agent")
    config.get_settings.cache_clear()
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/ws/voice") as ws:
            ws.receive_text()
    # Starlette may rewrite close codes; accept any non-OK close.
    assert excinfo.value.code != 1000


def test_voice_partial_exposes_latest_text(client: TestClient, chdir_state_tmp: Path) -> None:
    import json

    utterance = {
        "ts": "2026-06-13T10:01:02+00:00",
        "incident_id": "INC-VL-1",
        "phrase": "outage_detected",
        "voice": "en-GB-RyanNeural",
        "text": "Critical outage on FN-LDN-1.",
        "ssml": "<speak/>",
        "severity": "critical",
    }
    state = chdir_state_tmp / "state"
    state.mkdir(exist_ok=True)
    (state / "voice_outbox.jsonl").write_text(json.dumps(utterance) + "\n", encoding="utf-8")
    r = client.get("/partials/voice")
    assert r.status_code == 200
    assert 'data-latest-text="Critical outage on FN-LDN-1."' in r.text
    assert 'data-latest-voice="en-GB-RyanNeural"' in r.text
    assert 'data-latest-incident="INC-VL-1"' in r.text
