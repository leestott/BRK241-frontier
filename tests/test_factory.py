"""Agent factory + LocalAgent backend tests."""
from __future__ import annotations

import asyncio
import json

import pytest

from fibreops.agents import factory
from fibreops.agents.factory import (
    AgentBackend,
    LocalAgent,
    _resolve_backend,
    build_field_dispatch_agent,
    build_incident_analysis_agent,
    build_netops_coordinator_agent,
)
from fibreops import config


def _refresh_settings():
    config.get_settings.cache_clear()


def test_resolve_backend_explicit_arg_wins(monkeypatch):
    # FIBREOPS_AGENT_BACKEND says local but caller asks for foundry → caller wins.
    monkeypatch.setenv("FIBREOPS_AGENT_BACKEND", "local")
    _refresh_settings()
    assert _resolve_backend("foundry") == "foundry"


def test_resolve_backend_env_override(monkeypatch):
    monkeypatch.setenv("AZURE_AI_PROJECT_ENDPOINT", "https://example/")
    monkeypatch.setenv("FIBREOPS_AGENT_BACKEND", "local")
    _refresh_settings()
    # Even with Foundry configured, env override forces local.
    assert _resolve_backend(None) == AgentBackend.LOCAL


def test_resolve_backend_auto_without_foundry_is_local(monkeypatch):
    monkeypatch.setenv("FIBREOPS_AGENT_BACKEND", "auto")
    _refresh_settings()
    assert _resolve_backend(None) == AgentBackend.LOCAL


def test_resolve_backend_auto_with_foundry_no_registry_is_foundry(monkeypatch, tmp_path):
    # No published registry, but Foundry endpoint is set → auto picks foundry.
    monkeypatch.setenv("AZURE_AI_PROJECT_ENDPOINT", "https://example/")
    monkeypatch.setenv("FIBREOPS_AGENT_BACKEND", "auto")
    monkeypatch.chdir(tmp_path)  # ensure no state/foundry_agents.json
    _refresh_settings()
    assert _resolve_backend(None) == AgentBackend.FOUNDRY


def test_factory_returns_local_agent_for_each_role():
    a = build_incident_analysis_agent()
    b = build_netops_coordinator_agent()
    c = build_field_dispatch_agent()
    assert isinstance(a, LocalAgent) and a.role == "incident_analysis"
    assert isinstance(b, LocalAgent) and b.role == "netops_coordinator"
    assert isinstance(c, LocalAgent) and c.role == "field_dispatch"


def test_local_agent_unknown_role_raises():
    agent = LocalAgent(name="X", role="not_a_real_role", tools={})
    with pytest.raises(ValueError):
        asyncio.run(agent.run("{}"))


def test_local_incident_analysis_escalates_high_to_critical_on_many_customers(chdir_state_tmp):
    agent = build_incident_analysis_agent()
    payload = json.dumps(
        {
            "signal_id": "sig-1",
            "node_id": "FN-LDN-001",
            "site": "Shoreditch CO",
            "region": "London",
            "customers_served": 9000,
            "signal_type": "loss_of_light",
            "severity": "high",
            "measured_value": -40.0,
            "unit": "dBm",
            "raw": {"last_good_dbm": -18.0},
        }
    )
    response = asyncio.run(agent.run(payload))
    out = json.loads(response.text)
    assert out["severity"] == "critical"
    assert out["sop_refs"] == ["sop_loss_of_light"]
    assert "loss_of_light" in out["summary"]


def test_local_coordinator_dispatches_only_on_high_or_critical(monkeypatch, chdir_state_tmp):
    # The coordinator's create_ticket tool POSTs to mock-D365; stub the HTTP
    # transport so this test stays in-process.
    from fibreops.tools import ticketing

    def _fake_post(path, payload):
        return {"ticket_id": "TKT-FAKE", "status": "new", **payload}

    monkeypatch.setattr(ticketing, "_post", _fake_post)
    agent = build_netops_coordinator_agent()
    base_payload = {
        "incident_id": "INC-X",
        "node_id": "FN-LDN-001",
        "analysis": {
            "summary": "s",
            "probable_cause": "pc",
            "customer_impact": "ci",
            "severity": "medium",
            "recommended_actions": ["a1"],
            "sop_refs": ["sop_loss_of_light"],
        },
    }
    medium_resp = asyncio.run(agent.run(json.dumps(base_payload)))
    assert medium_resp.metadata["decision"] == "MONITOR"

    high = dict(base_payload)
    high["incident_id"] = "INC-Y"
    high["analysis"] = dict(base_payload["analysis"], severity="critical")
    high_resp = asyncio.run(agent.run(json.dumps(high)))
    assert high_resp.metadata["decision"] == "HANDOFF:DISPATCH"
    assert "ticket_id" in high_resp.metadata["ticket"]


def test_local_field_dispatch_returns_no_engineer_when_all_off_shift(monkeypatch, chdir_state_tmp):
    from fibreops.tools import dispatch as dispatch_tool

    def _all_off_shift():
        return [
            {
                "engineer_id": "ENG-ZZ",
                "name": "Test",
                "region": "London",
                "skills": ["splicing", "OTDR"],
                "on_shift": False,
                "distance_km_from_node": {"FN-LDN-001": 1.0},
            }
        ]

    monkeypatch.setattr(dispatch_tool, "_engineers", _all_off_shift)
    agent = build_field_dispatch_agent()
    payload = json.dumps(
        {
            "incident_id": "INC-Z",
            "ticket_id": "TKT-Z",
            "node_id": "FN-LDN-001",
            "signal_type": "loss_of_light",
            "severity": "critical",
        }
    )
    response = asyncio.run(agent.run(payload))
    assert "NO_ENGINEER_AVAILABLE" in response.text
    assert response.metadata["dispatch"]["dispatched"] is False
