"""Voice Live integration tests.

Covers the local outbox path (default) plus the configured webhook path with
a stubbed httpx client, the orchestrator opt-in flag, and the UI action that
speaks for the latest incident.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import fibreops.ui.app as ui_module
from fibreops.tools import speak_status_update
from fibreops.tools import voice as voice_module


def _voice_path(state: Path) -> Path:
    return state / "voice_outbox.jsonl"


def test_speak_status_update_writes_to_outbox_by_default(chdir_state_tmp: Path) -> None:
    out = speak_status_update(
        incident_id="INC-001",
        phrase="outage_detected",
        severity="critical",
        node_id="FN-LDN-001",
        region="London",
        customers=8200,
        probable_cause="fibre cut",
    )
    voice_file = _voice_path(chdir_state_tmp / "state")
    assert voice_file.exists()
    line = voice_file.read_text(encoding="utf-8").splitlines()[-1]
    payload = json.loads(line)
    assert payload["incident_id"] == "INC-001"
    assert payload["phrase"] == "outage_detected"
    assert payload["severity"] == "critical"
    assert "FN-LDN-001" in payload["text"]
    assert "London" in payload["text"]
    assert "8,200" in payload["text"]
    # SSML envelope is well-formed and uses a critical voice.
    assert payload["ssml"].startswith("<speak")
    assert "en-GB-RyanNeural" in payload["voice"] or payload["voice"].startswith("en-GB-")
    assert "<mstts:express-as" in payload["ssml"]
    assert out["delivery"]["status"] == "logged-locally"


def test_speak_status_update_dispatched_phrase(chdir_state_tmp: Path) -> None:
    out = speak_status_update(
        incident_id="INC-007",
        phrase="engineer_dispatched",
        severity="high",
        engineer="Priya Shah",
        eta=18,
    )
    assert "Priya Shah" in out["text"]
    assert "18" in out["text"]
    assert out["severity"] == "high"


def test_speak_status_update_unknown_phrase_raises(chdir_state_tmp: Path) -> None:
    with pytest.raises(KeyError):
        speak_status_update(incident_id="INC-X", phrase="not_a_real_phrase")


def test_speak_status_update_uses_configured_endpoint(
    chdir_state_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AZURE_VOICE_LIVE_ENDPOINT", "https://voice.example.com/synthesize")
    monkeypatch.setenv("AZURE_VOICE_LIVE_API_KEY", "abc123")
    monkeypatch.setenv("AZURE_VOICE_LIVE_VOICE", "en-GB-SoniaNeural")
    from fibreops import config

    config.get_settings.cache_clear()

    captured: dict[str, object] = {}

    class _FakeResp:
        status_code = 202

        def raise_for_status(self) -> None:
            return None

    class _FakeClient:
        def __init__(self, *_, **__):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def post(self, url, json, headers):  # noqa: A002 - mirror httpx API
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _FakeResp()

    monkeypatch.setattr(voice_module.httpx, "Client", _FakeClient)

    out = speak_status_update(
        incident_id="INC-009",
        phrase="outage_detected",
        severity="medium",
        node_id="FN-MAN-002",
        region="Manchester",
        customers=300,
        probable_cause="splice degradation",
    )

    assert captured["url"] == "https://voice.example.com/synthesize"
    assert captured["headers"]["Ocp-Apim-Subscription-Key"] == "abc123"
    sent = captured["json"]
    assert sent["voice"] == "en-GB-SoniaNeural"
    assert sent["incident_id"] == "INC-009"
    assert out["delivery"]["status"] == "sent"
    # The outbox is always written (the UI reads data-latest-text from it) in
    # addition to the server-side POST when an endpoint is configured.
    assert _voice_path(chdir_state_tmp / "state").exists()


def test_local_agent_emits_voice_when_enabled(
    chdir_state_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The LocalAgent NetOps coordinator speaks when FIBREOPS_VOICE_UPDATES is set."""
    import asyncio

    monkeypatch.setenv("FIBREOPS_VOICE_UPDATES", "1")
    from fibreops import config

    config.get_settings.cache_clear()

    # Avoid real network calls — stub the network-touching tools at the
    # module level (the LocalAgent's tool dict pulls fresh references each
    # call via the names captured in factory).
    from fibreops.tools import ticketing as _ticketing
    monkeypatch.setattr(
        _ticketing, "create_ticket", lambda **kw: {"ticket_id": "TKT-V1", "status": "new"}
    )
    from fibreops.agents import factory as _factory
    monkeypatch.setattr(
        _factory, "create_ticket", lambda **kw: {"ticket_id": "TKT-V1", "status": "new"}
    )
    monkeypatch.setattr(_factory, "post_outage_notice", lambda **kw: {"status": "logged-locally"})
    monkeypatch.setattr(_factory, "remember", lambda **kw: {"ok": True})

    from fibreops.agents.factory import build_netops_coordinator_agent

    agent = build_netops_coordinator_agent(prefer="local", prefer_routine=False)
    prompt = json.dumps(
        {
            "incident_id": "INC-VOICE-1",
            "node_id": "FN-LDN-001",
            "region": "London",
            "customers_served": 4000,
            "analysis": {
                "summary": "loss_of_light on FN-LDN-001",
                "probable_cause": "fibre cut",
                "customer_impact": "~4,000 customers",
                "severity": "critical",
                "recommended_actions": ["dispatch", "open ticket"],
                "sop_refs": ["sop_loss_of_light"],
            },
        }
    )
    asyncio.run(agent.run(prompt))

    voice_lines = _voice_path(chdir_state_tmp / "state").read_text(encoding="utf-8").splitlines()
    payloads = [json.loads(line) for line in voice_lines if line.strip()]
    matching = [p for p in payloads if p["incident_id"] == "INC-VOICE-1"]
    assert matching, f"expected voice utterance for INC-VOICE-1, got: {payloads}"
    assert matching[0]["phrase"] == "outage_detected"


def test_local_agent_silent_when_voice_disabled(
    chdir_state_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FIBREOPS_VOICE_UPDATES", raising=False)
    from fibreops import config

    config.get_settings.cache_clear()

    from fibreops.agents import factory as _factory
    monkeypatch.setattr(_factory, "create_ticket", lambda **kw: {"ticket_id": "TKT-S1", "status": "new"})
    monkeypatch.setattr(_factory, "post_outage_notice", lambda **kw: {"status": "logged-locally"})
    monkeypatch.setattr(_factory, "remember", lambda **kw: {"ok": True})

    import asyncio
    from fibreops.agents.factory import build_netops_coordinator_agent

    agent = build_netops_coordinator_agent(prefer="local", prefer_routine=False)
    prompt = json.dumps(
        {
            "incident_id": "INC-SILENT",
            "node_id": "FN-LDN-001",
            "analysis": {
                "summary": "x",
                "probable_cause": "y",
                "customer_impact": "z",
                "severity": "critical",
                "recommended_actions": ["a"],
                "sop_refs": ["sop_loss_of_light"],
            },
        }
    )
    asyncio.run(agent.run(prompt))
    assert not _voice_path(chdir_state_tmp / "state").exists()


# --- UI -------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, chdir_state_tmp: Path) -> TestClient:
    monkeypatch.setenv("FIBREOPS_UI_SKIP_MOCK_D365", "1")
    return TestClient(ui_module.app)


def _write_run_for_ui(state: Path, *, dispatched: bool) -> None:
    state.mkdir(exist_ok=True)
    run = {
        "run_id": "run-ui1",
        "incident_id": "INC-UI-1",
        "started_at": "2026-06-13T10:00:00+00:00",
        "signal": {"node_id": "FN-LDN-001", "signal_type": "loss_of_light", "severity": "critical"},
        "node_context": {"region": "London", "site": "Shoreditch", "customers_served": 6000},
        "steps": [
            {
                "agent": "IncidentAnalysisAgent",
                "output": {
                    "summary": "LoS on FN-LDN-001",
                    "probable_cause": "fibre cut",
                    "customer_impact": "~6,000 customers",
                    "severity": "critical",
                    "recommended_actions": ["dispatch"],
                    "sop_refs": ["sop_loss_of_light"],
                },
            }
        ],
    }
    if dispatched:
        run["steps"].append(
            {
                "agent": "FieldDispatchAgent",
                "result": "DISPATCHED Priya Shah ETA 22 min",
                "metadata": {"dispatch": {"engineer_name": "Priya Shah", "eta_minutes": 22}},
            }
        )
    (state / "runs.jsonl").open("a", encoding="utf-8").write(json.dumps(run) + "\n")


def test_voice_partial_empty_state(client: TestClient) -> None:
    r = client.get("/partials/voice")
    assert r.status_code == 200
    assert "No voice updates yet" in r.text


def test_voice_partial_renders_outbox(client: TestClient, chdir_state_tmp: Path) -> None:
    utterance = {
        "ts": "2026-06-13T10:01:02+00:00",
        "incident_id": "INC-UI-VV",
        "phrase": "outage_detected",
        "voice": "en-GB-RyanNeural",
        "text": "Heads up team — critical fibre outage on FN-LDN-001.",
        "ssml": "<speak/>",
        "severity": "critical",
    }
    (chdir_state_tmp / "state" / "voice_outbox.jsonl").write_text(
        json.dumps(utterance) + "\n", encoding="utf-8"
    )
    r = client.get("/partials/voice")
    assert r.status_code == 200
    assert "INC-UI-VV" in r.text
    assert "en-GB-RyanNeural" in r.text
    assert "CRITICAL" in r.text
    assert "Heads up team" in r.text


def test_action_voice_dispatched_phrase(client: TestClient, chdir_state_tmp: Path) -> None:
    _write_run_for_ui(chdir_state_tmp / "state", dispatched=True)
    r = client.post("/actions/voice")
    assert r.status_code == 200
    voice_lines = (chdir_state_tmp / "state" / "voice_outbox.jsonl").read_text(encoding="utf-8").splitlines()
    payload = json.loads(voice_lines[-1])
    assert payload["phrase"] == "engineer_dispatched"
    assert "Priya Shah" in payload["text"]
    assert "22" in payload["text"]


def test_action_voice_outage_phrase_when_not_dispatched(
    client: TestClient, chdir_state_tmp: Path
) -> None:
    _write_run_for_ui(chdir_state_tmp / "state", dispatched=False)
    r = client.post("/actions/voice")
    assert r.status_code == 200
    voice_lines = (chdir_state_tmp / "state" / "voice_outbox.jsonl").read_text(encoding="utf-8").splitlines()
    payload = json.loads(voice_lines[-1])
    assert payload["phrase"] == "outage_detected"
    assert "FN-LDN-001" in payload["text"]
    assert "London" in payload["text"]


def test_action_voice_no_runs_is_noop(client: TestClient, chdir_state_tmp: Path) -> None:
    r = client.post("/actions/voice")
    assert r.status_code == 200
    assert "No voice updates yet" in r.text
    assert not (chdir_state_tmp / "state" / "voice_outbox.jsonl").exists()


def test_action_reset_clears_voice_outbox(client: TestClient, chdir_state_tmp: Path) -> None:
    voice = chdir_state_tmp / "state" / "voice_outbox.jsonl"
    voice.write_text(json.dumps({"x": 1}) + "\n", encoding="utf-8")
    assert voice.exists()
    r = client.post("/actions/reset")
    assert r.status_code == 200
    assert not voice.exists()
