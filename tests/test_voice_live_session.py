"""Tests for the Voice Live session/proxy plumbing.

The realtime upstream is not contacted in tests — we verify URL/headers
construction, the public /api/voice/session descriptor, and that the
WebSocket proxy refuses connections cleanly when nothing is configured.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import fibreops.ui.app as ui_module
from fibreops import config
from fibreops.voice_live import (
    build_upstream_headers,
    build_upstream_url,
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


def test_build_upstream_url_unconfigured() -> None:
    config.get_settings.cache_clear()
    assert build_upstream_url() is None


def test_build_upstream_url_appends_realtime_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # Agent mode: the upstream URL embeds a bearer token, so stub the token.
    monkeypatch.setattr("fibreops.voice_live._get_bearer_token", lambda: "tok")
    _reload_settings(
        monkeypatch,
        AZURE_VOICE_LIVE_ENDPOINT="https://eastus.api.cognitive.microsoft.com",
        AZURE_VOICE_LIVE_AGENT_ID="agent-123",
        AZURE_VOICE_LIVE_API_VERSION="2025-05-01-preview",
    )
    url = build_upstream_url()
    assert url is not None
    assert url.startswith("wss://eastus.api.cognitive.microsoft.com/voice-live/realtime?")
    assert "api-version=2025-05-01-preview" in url
    assert "agent-name=agent-123" in url
    assert "authorization=Bearer+tok" in url


def test_build_upstream_url_normalises_to_host_and_realtime_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # The proxy extracts the bare host and always targets /voice-live/realtime,
    # in direct-model mode (no agent id) with the Voice Live managed model name.
    _reload_settings(
        monkeypatch,
        AZURE_VOICE_LIVE_ENDPOINT="wss://example.com/voicelive/realtime?foo=bar",
        AZURE_VOICE_LIVE_MODEL="gpt-4o-mini",
    )
    url = build_upstream_url()
    assert url is not None
    assert url.startswith("wss://example.com/voice-live/realtime?")
    assert "api-version=" in url
    assert "model=gpt-4o-mini" in url


def test_build_upstream_headers_includes_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # Runtime prefers the managed-identity bearer token; the api-key header is a
    # local-dev fallback only used when no token is available.
    monkeypatch.setattr("fibreops.voice_live._get_bearer_token", lambda: None)
    _reload_settings(
        monkeypatch,
        AZURE_VOICE_LIVE_ENDPOINT="https://example.com",
        AZURE_VOICE_LIVE_API_KEY="sekret",
    )
    assert build_upstream_headers() == {"api-key": "sekret"}


def test_session_descriptor_disabled_by_default() -> None:
    config.get_settings.cache_clear()
    desc = session_descriptor()
    assert desc["enabled"] is False
    assert desc["ws_path"] is None
    assert desc["duplex_enabled"] is False


def test_session_descriptor_enabled_one_shot(monkeypatch: pytest.MonkeyPatch) -> None:
    _reload_settings(
        monkeypatch,
        AZURE_VOICE_LIVE_ENDPOINT="https://example.com",
        AZURE_VOICE_LIVE_VOICE="en-GB-RyanNeural",
    )
    desc = session_descriptor()
    assert desc["enabled"] is True
    assert desc["ws_path"] == "/ws/voice"
    assert desc["voice"] == "en-GB-RyanNeural"
    # Duplex mic works in direct-model mode (no agent id required).
    assert desc["duplex_enabled"] is True


def test_session_descriptor_duplex_when_agent_id_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _reload_settings(
        monkeypatch,
        AZURE_VOICE_LIVE_ENDPOINT="https://example.com",
        AZURE_VOICE_LIVE_AGENT_ID="agent-XYZ",
    )
    desc = session_descriptor()
    assert desc["duplex_enabled"] is True
    assert desc["agent_id"] == "agent-XYZ"


def test_api_voice_session_endpoint(client: TestClient) -> None:
    config.get_settings.cache_clear()
    r = client.get("/api/voice/session")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) >= {"enabled", "ws_path", "voice", "duplex_enabled"}


def test_ws_voice_closes_when_unconfigured(client: TestClient) -> None:
    """Without AZURE_VOICE_LIVE_ENDPOINT the proxy must refuse cleanly."""
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
