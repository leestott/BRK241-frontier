"""GitHub Copilot SDK adapter tests.

Covers session lifecycle, prompt-shape routing (telemetry signal vs free-form
chat), turn history, the FastAPI ``/sdk/chat`` endpoint, and the demo ``chat``
CLI subcommand.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import fibreops.ui.app as ui_module
from fibreops.sdk import FibreOpsCopilotClient
from fibreops.sdk import copilot_client as sdk_module


# --- session lifecycle --------------------------------------------------------


def test_client_creates_unique_sessions(chdir_state_tmp: Path) -> None:
    async def _run() -> None:
        client = FibreOpsCopilotClient()
        s1 = await client.create_session()
        s2 = await client.create_session()
        assert s1.session_id != s2.session_id
        assert s1.session_id.startswith("cs-")
        assert client.get_session(s1.session_id) is s1
        assert client.get_session("does-not-exist") is None
        await s1.close()
        assert s1.closed

    asyncio.run(_run())


def test_session_send_after_close_raises(chdir_state_tmp: Path) -> None:
    async def _run() -> None:
        client = FibreOpsCopilotClient()
        session = await client.create_session()
        await session.close()
        with pytest.raises(RuntimeError):
            await session.send_and_wait("status")

    asyncio.run(_run())


# --- chat prompts -------------------------------------------------------------


def test_chat_help_prompt(chdir_state_tmp: Path) -> None:
    async def _run() -> None:
        client = FibreOpsCopilotClient()
        session = await client.create_session()
        resp = await session.send_and_wait("help")
        assert resp.kind == "chat"
        assert "status" in resp.text
        assert "nodes" in resp.text
        assert resp.session_id == session.session_id
        # Turn history recorded.
        assert len(session.turns) == 1
        assert session.turns[0]["response"]["kind"] == "chat"

    asyncio.run(_run())


def test_chat_status_no_runs(chdir_state_tmp: Path) -> None:
    async def _run() -> None:
        client = FibreOpsCopilotClient()
        session = await client.create_session()
        resp = await session.send_and_wait("what's the status?")
        assert "No incidents recorded yet" in resp.text

    asyncio.run(_run())


def test_chat_status_summarises_latest_run(chdir_state_tmp: Path) -> None:
    state = chdir_state_tmp / "state"
    state.mkdir(exist_ok=True)
    run = {
        "run_id": "run-x",
        "incident_id": "INC-CSDK-1",
        "signal": {"node_id": "FN-LDN-001", "signal_type": "loss_of_light", "severity": "critical"},
        "steps": [
            {
                "agent": "IncidentAnalysisAgent",
                "output": {
                    "summary": "loss_of_light on FN-LDN-001",
                    "severity": "critical",
                    "probable_cause": "fibre cut",
                },
            },
            {
                "agent": "NetOpsCoordinatorAgent",
                "decision": "HANDOFF:DISPATCH auto",
                "ticket": {"ticket_id": "TKT-XYZ"},
            },
            {
                "agent": "FieldDispatchAgent",
                "result": "DISPATCHED Priya Shah ETA 22 min",
            },
        ],
    }
    (state / "runs.jsonl").write_text(json.dumps(run) + "\n", encoding="utf-8")

    async def _run() -> None:
        client = FibreOpsCopilotClient()
        session = await client.create_session()
        resp = await session.send_and_wait("latest")
        assert "INC-CSDK-1" in resp.text
        assert "TKT-XYZ" in resp.text
        assert "Priya Shah" in resp.text
        assert resp.data["runs_loaded"] == 1

    asyncio.run(_run())


def test_chat_nodes_lists_topology(chdir_state_tmp: Path) -> None:
    async def _run() -> None:
        client = FibreOpsCopilotClient()
        session = await client.create_session()
        resp = await session.send_and_wait("show me the nodes")
        assert "FN-LDN-001" in resp.text
        assert "Manchester" in resp.text

    asyncio.run(_run())


def test_chat_unknown_prompt_routes_to_default(chdir_state_tmp: Path) -> None:
    async def _run() -> None:
        client = FibreOpsCopilotClient()
        session = await client.create_session()
        resp = await session.send_and_wait("xyzzy random query")
        assert "help" in resp.text.lower()

    asyncio.run(_run())


# --- signal prompts -----------------------------------------------------------


def test_signal_shaped_prompt_runs_orchestrator(
    chdir_state_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dict with signal fields hits the orchestrator and returns the record."""
    captured = {}

    async def _fake_handle_signal(signal):
        captured["signal"] = signal
        return {
            "run_id": "run-sdk-1",
            "incident_id": "INC-SDK-1",
            "signal": signal.model_dump(mode="json"),
            "steps": [
                {
                    "agent": "IncidentAnalysisAgent",
                    "output": {
                        "summary": "test summary",
                        "severity": "critical",
                        "probable_cause": "test cause",
                    },
                },
                {
                    "agent": "NetOpsCoordinatorAgent",
                    "decision": "HANDOFF:DISPATCH test",
                    "ticket": {"ticket_id": "TKT-SDK"},
                },
            ],
        }

    from fibreops import orchestrator
    monkeypatch.setattr(orchestrator, "handle_signal", _fake_handle_signal)

    async def _run() -> None:
        client = FibreOpsCopilotClient()
        session = await client.create_session()
        payload = {
            "signal_id": "SIG-CSDK-1",
            "node_id": "FN-LDN-001",
            "signal_type": "loss_of_light",
            "severity": "critical",
            "measured_value": -40.0,
            "unit": "dBm",
            "raw": {"last_good_dbm": -22.5},
        }
        resp = await session.send_and_wait(payload)
        assert resp.kind == "signal"
        assert "INC-SDK-1" in resp.text
        assert "TKT-SDK" in resp.text
        assert resp.data["run_id"] == "run-sdk-1"

    asyncio.run(_run())
    assert captured["signal"].signal_id == "SIG-CSDK-1"


def test_signal_shaped_json_string_is_parsed(
    chdir_state_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_handle_signal(signal):
        return {"incident_id": "INC-PARSED", "signal": {}, "steps": []}

    from fibreops import orchestrator
    monkeypatch.setattr(orchestrator, "handle_signal", _fake_handle_signal)

    async def _run() -> None:
        client = FibreOpsCopilotClient()
        session = await client.create_session()
        text = json.dumps(
            {
                "signal_id": "SIG-PARSE",
                "node_id": "FN-LDN-001",
                "signal_type": "high_attenuation",
                "severity": "high",
            }
        )
        resp = await session.send_and_wait(text)
        assert resp.kind == "signal"
        assert "INC-PARSED" in resp.text

    asyncio.run(_run())


def test_invalid_json_falls_back_to_chat(chdir_state_tmp: Path) -> None:
    async def _run() -> None:
        client = FibreOpsCopilotClient()
        session = await client.create_session()
        resp = await session.send_and_wait("{not really json")
        # Falls through to chat default.
        assert resp.kind == "chat"

    asyncio.run(_run())


def test_turn_history_caps(chdir_state_tmp: Path) -> None:
    async def _run() -> None:
        client = FibreOpsCopilotClient(max_session_turns=3)
        session = await client.create_session()
        for i in range(5):
            await session.send_and_wait(f"help {i}")
        assert len(session.turns) == 3

    asyncio.run(_run())


# --- UI endpoint --------------------------------------------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, chdir_state_tmp: Path) -> TestClient:
    monkeypatch.setenv("FIBREOPS_UI_SKIP_MOCK_D365", "1")
    return TestClient(ui_module.app)


def test_sdk_chat_endpoint_returns_response(client: TestClient) -> None:
    r = client.post("/sdk/chat", json={"prompt": "help"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "chat"
    assert "status" in body["text"]
    assert body["session_id"].startswith("cs-")


def test_sdk_chat_endpoint_requires_prompt(client: TestClient) -> None:
    r = client.post("/sdk/chat", json={})
    assert r.status_code == 400


# --- CLI ----------------------------------------------------------------------


def test_chat_cli_command(chdir_state_tmp: Path) -> None:
    from fibreops.demo import app as demo_app

    runner = CliRunner()
    result = runner.invoke(demo_app, ["chat", "help"])
    assert result.exit_code == 0, result.output
    assert "Copilot SDK" in result.output
    assert "status" in result.output
