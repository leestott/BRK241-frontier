"""FibreOps Operations Console tests.

In-process FastAPI ``TestClient`` against the UI app. We monkeypatch
``handle_signal`` for the inject action so the test stays hermetic (no
need to spin up the mock-D365 lifespan in unit tests).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import fibreops.ui.app as ui_module


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, chdir_state_tmp: Path) -> TestClient:
    # Skip the mock-D365 spawn during lifespan — the inject test stubs the
    # orchestrator so we don't need a real D365 endpoint.
    monkeypatch.setenv("FIBREOPS_UI_SKIP_MOCK_D365", "1")
    return TestClient(ui_module.app)


def _write_run(state_dir: Path, **overrides) -> dict:
    state_dir.mkdir(exist_ok=True)
    run = {
        "run_id": "run-aaaa1111",
        "incident_id": "INC-AAA",
        "started_at": "2026-06-13T10:00:00+00:00",
        "signal": {
            "node_id": "FN-LDN-001",
            "signal_type": "loss_of_light",
            "severity": "critical",
        },
        "node_context": {"region": "London", "site": "Shoreditch CO", "customers_served": 8200},
        "steps": [
            {
                "agent": "IncidentAnalysisAgent",
                "output": {
                    "summary": "LoS on FN-LDN-001",
                    "probable_cause": "fibre cut",
                    "customer_impact": "~8,200 customers",
                    "severity": "critical",
                    "recommended_actions": ["dispatch splicing engineer"],
                    "sop_refs": ["sop_loss_of_light"],
                },
            },
            {
                "agent": "NetOpsCoordinatorAgent",
                "decision": "HANDOFF:DISPATCH auto-dispatch threshold met",
                "ticket": {"ticket_id": "TKT-XYZ", "status": "new"},
            },
            {
                "agent": "FieldDispatchAgent",
                "result": "DISPATCHED Priya Shah ETA 18 min",
                "metadata": {
                    "dispatch": {
                        "engineer_name": "Priya Shah",
                        "eta_minutes": 18,
                        "booking": {"booking_id": "BK-ABC123"},
                    }
                },
            },
        ],
    }
    run.update(overrides)
    runs_file = state_dir / "runs.jsonl"
    with runs_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(run) + "\n")
    return run


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_index_renders_with_backend_badge(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "FibreOps NOC Console" in r.text
    assert "backend" in r.text
    # Backend is forced to "local" by conftest's hermetic_env fixture.
    assert "local" in r.text


def test_runs_partial_empty(client):
    r = client.get("/partials/runs")
    assert r.status_code == 200
    assert "No incidents yet" in r.text


def test_runs_partial_renders_severity_and_engineer(client, chdir_state_tmp):
    _write_run(chdir_state_tmp / "state")
    r = client.get("/partials/runs")
    assert r.status_code == 200
    assert "FN-LDN-001" in r.text
    assert "Priya Shah" in r.text
    assert "ETA 18 min" in r.text
    assert "sev-critical" in r.text


def test_run_detail_renders_agent_timeline(client, chdir_state_tmp):
    _write_run(chdir_state_tmp / "state")
    r = client.get("/partials/run/run-aaaa1111")
    assert r.status_code == 200
    assert "IncidentAnalysisAgent" in r.text
    assert "NetOpsCoordinatorAgent" in r.text
    assert "FieldDispatchAgent" in r.text
    assert "TKT-XYZ" in r.text
    assert "BK-ABC123" in r.text
    assert "sop_loss_of_light" in r.text


def test_run_detail_for_unknown_id_returns_not_found_message(client):
    r = client.get("/partials/run/run-does-not-exist")
    assert r.status_code == 200
    assert "Run not found" in r.text


def test_optimiser_partial_empty(client):
    r = client.get("/partials/optimiser")
    assert r.status_code == 200
    assert "No runs scored yet" in r.text


def test_optimiser_partial_with_summary(client, chdir_state_tmp):
    summary = {
        "runs": 3,
        "avg_score": 0.83,
        "scores": [
            {"criteria": {"analysis_completeness": 1.0, "severity_consistency": 1.0,
                          "ticket_created": 1.0, "dispatch_policy": 0.5, "sop_referenced": 1.0}},
            {"criteria": {"analysis_completeness": 1.0, "severity_consistency": 1.0,
                          "ticket_created": 1.0, "dispatch_policy": 1.0, "sop_referenced": 1.0}},
        ],
        "suggestions": [
            {"target": "FieldDispatchAgent.instructions",
             "change": "Broaden region on no-engineer",
             "evidence": "1 run(s) failed dispatch"}
        ],
    }
    (chdir_state_tmp / "state").mkdir(exist_ok=True)
    (chdir_state_tmp / "state" / "optimiser_suggestions.jsonl").write_text(
        json.dumps(summary), encoding="utf-8"
    )
    r = client.get("/partials/optimiser")
    assert r.status_code == 200
    assert "0.83" in r.text
    assert "3 runs" in r.text
    assert "FieldDispatchAgent.instructions" in r.text
    assert "analysis_completeness" in r.text


def test_teams_partial_renders_flattened_card(client, chdir_state_tmp):
    card = {
        "type": "message",
        "attachments": [{"contentType": "x", "content": {"body": [
            {"type": "TextBlock", "text": "🚨 Fibre outage detected — CRITICAL"},
            {"type": "TextBlock", "text": "LoS on FN-LDN-001"},
            {"type": "FactSet", "facts": [
                {"title": "Incident", "value": "INC-AAA"},
                {"title": "Node", "value": "FN-LDN-001"},
            ]},
        ]}}],
    }
    (chdir_state_tmp / "state").mkdir(exist_ok=True)
    (chdir_state_tmp / "state" / "teams_outbox.jsonl").write_text(
        json.dumps(card) + "\n", encoding="utf-8"
    )
    r = client.get("/partials/teams")
    assert r.status_code == 200
    assert "Fibre outage detected" in r.text
    assert "INC-AAA" in r.text


def test_sim_partial_shows_start_when_off(client):
    r = client.get("/partials/sim")
    assert r.status_code == 200
    assert "Start simulation" in r.text


def test_action_reset_truncates_state(client, chdir_state_tmp):
    state = chdir_state_tmp / "state"
    _write_run(state)
    assert (state / "runs.jsonl").exists()
    r = client.post("/actions/reset")
    assert r.status_code == 200
    assert not (state / "runs.jsonl").exists()
    assert "No incidents yet" in r.text


def test_action_inject_invokes_handle_signal(client, monkeypatch, chdir_state_tmp):
    calls: list[object] = []

    async def fake_handle_signal(sig):
        calls.append(sig)
        # Persist a fake run so the partial reflects it.
        _write_run(chdir_state_tmp / "state", run_id=f"run-fake-{len(calls)}")
        return {"run_id": f"run-fake-{len(calls)}"}

    monkeypatch.setattr(
        "fibreops.orchestrator.handle_signal", fake_handle_signal
    )
    r = client.post("/actions/inject?count=2")
    assert r.status_code == 200
    assert len(calls) == 2
    assert "FN-LDN-001" in r.text


def test_action_inject_clamps_count_between_1_and_10(client, monkeypatch):
    calls: list[object] = []

    async def fake_handle_signal(sig):
        calls.append(sig)
        return {"run_id": "x"}

    monkeypatch.setattr("fibreops.orchestrator.handle_signal", fake_handle_signal)
    client.post("/actions/inject?count=999")
    assert len(calls) == 10
    calls.clear()
    client.post("/actions/inject?count=0")
    assert len(calls) == 1


def test_api_runs_returns_json(client, chdir_state_tmp):
    _write_run(chdir_state_tmp / "state")
    r = client.get("/api/runs")
    assert r.status_code == 200
    body = r.json()
    assert "runs" in body
    assert body["runs"][0]["run_id"] == "run-aaaa1111"


def test_api_optimiser_returns_null_when_unrun(client):
    r = client.get("/api/optimiser")
    assert r.status_code == 200
    assert r.json() == {"summary": None}


def test_simulate_toggle_on_then_off(client):
    r = client.post("/actions/simulate/on")
    assert r.status_code == 200
    assert "Stop simulation" in r.text
    r = client.post("/actions/simulate/off")
    assert r.status_code == 200
    assert "Start simulation" in r.text


def test_simulate_invalid_state_400(client):
    r = client.post("/actions/simulate/sideways")
    assert r.status_code == 400
