"""Foundry Routines tests.

Covers the NetOps coordinator routine: deterministic 3-step plan, decision
expression, factory selection via FIBREOPS_NETOPS_ROUTINE, and the routine's
trace metadata so the UI/optimiser can read it.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from fibreops.agents.routines import (
    NETOPS_ROUTINE_DEFINITION,
    NetOpsRoutineAgent,
    RoutineStep,
    _eval_decision,
    _resolve_value,
)


def _payload(severity: str = "critical") -> str:
    return json.dumps(
        {
            "incident_id": "INC-RT-1",
            "node_id": "FN-LDN-001",
            "analysis": {
                "summary": "LoS on FN-LDN-001",
                "probable_cause": "fibre cut",
                "customer_impact": "~6,000 customers",
                "severity": severity,
                "recommended_actions": ["dispatch splicing engineer", "open ticket"],
                "sop_refs": ["sop_loss_of_light"],
            },
        }
    )


def test_routine_definition_has_required_steps() -> None:
    names = [s.name for s in NETOPS_ROUTINE_DEFINITION.steps]
    assert names == ["file_ticket", "post_teams_notice", "remember_ticket"]
    assert "HANDOFF:DISPATCH" in NETOPS_ROUTINE_DEFINITION.decision
    assert "MONITOR" in NETOPS_ROUTINE_DEFINITION.decision


def test_resolve_value_returns_native_type_for_single_placeholder() -> None:
    ctx = {"ticket": {"ticket_id": "T-1", "status": "new"}}
    out = _resolve_value("{ticket}", ctx)
    assert out == {"ticket_id": "T-1", "status": "new"}


def test_resolve_value_does_string_formatting_for_mixed_placeholders() -> None:
    ctx = {"analysis": {"severity_upper": "CRITICAL", "summary": "LoS"}}
    out = _resolve_value("[{analysis.severity_upper}] {analysis.summary}", ctx)
    assert out == "[CRITICAL] LoS"


def test_eval_decision_handles_severity_membership() -> None:
    assert _eval_decision("severity in ('high','critical')", {"severity": "critical"}) is True
    assert _eval_decision("severity in ('high','critical')", {"severity": "low"}) is False


def test_eval_decision_rejects_unsafe_expression() -> None:
    # __import__ is gone from the safe globals; the expression must fail closed.
    assert _eval_decision("__import__('os').system('echo hax')", {}) is False


def test_routine_runs_full_plan_and_handoffs_on_critical(chdir_state_tmp: Path) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_create_ticket(**kwargs):
        calls.append(("create_ticket", kwargs))
        return {"ticket_id": "TKT-001", "status": "new"}

    def fake_post_outage_notice(**kwargs):
        calls.append(("post_outage_notice", kwargs))
        return {"status": "logged-locally"}

    def fake_remember(**kwargs):
        calls.append(("remember", kwargs))
        return {"ok": True}

    agent = NetOpsRoutineAgent(
        tools={
            "create_ticket": fake_create_ticket,
            "post_outage_notice": fake_post_outage_notice,
            "remember": fake_remember,
        }
    )
    resp = asyncio.run(agent.run(_payload("critical")))

    # Three steps were called in order with the right shape.
    assert [c[0] for c in calls] == [
        "create_ticket",
        "post_outage_notice",
        "remember",
    ]
    create_kw = calls[0][1]
    assert create_kw["incident_id"] == "INC-RT-1"
    assert create_kw["node_id"] == "FN-LDN-001"
    assert create_kw["severity"] == "critical"
    assert create_kw["title"].startswith("[CRITICAL]")
    assert "dispatch splicing engineer; open ticket" in create_kw["description"]

    # The remember step packed the captured ticket id back in.
    remember_kw = calls[2][1]
    assert remember_kw["scope"] == "global"
    assert remember_kw["key"] == "last_ticket_for_node:FN-LDN-001"
    assert remember_kw["value"] == {"ticket_id": "TKT-001", "severity": "critical"}

    # Critical severity triggers HANDOFF.
    assert resp.text.startswith("HANDOFF:DISPATCH")
    assert resp.metadata["decision"] == "HANDOFF:DISPATCH"
    assert resp.metadata["ticket"] == {"ticket_id": "TKT-001", "status": "new"}
    trace_steps = resp.metadata["routine"]["steps"]
    assert [s["step"] for s in trace_steps] == [
        "file_ticket",
        "post_teams_notice",
        "remember_ticket",
    ]
    assert all(s["status"] == "ok" for s in trace_steps)
    assert resp.metadata["routine"]["name"] == "netops-coordinator-v1"


def test_routine_decides_monitor_for_low_severity(chdir_state_tmp: Path) -> None:
    agent = NetOpsRoutineAgent(
        tools={
            "create_ticket": lambda **kw: {"ticket_id": "TKT-LOW", "status": "new"},
            "post_outage_notice": lambda **kw: {"status": "logged-locally"},
            "remember": lambda **kw: {"ok": True},
        }
    )
    resp = asyncio.run(agent.run(_payload("low")))
    assert resp.text.startswith("MONITOR")
    assert resp.metadata["decision"] == "MONITOR"


def test_routine_raises_when_required_tool_is_missing() -> None:
    agent = NetOpsRoutineAgent(tools={})
    with pytest.raises(RuntimeError, match="create_ticket"):
        asyncio.run(agent.run(_payload("critical")))


# --- factory integration --------------------------------------------------------


def test_factory_returns_routine_when_env_flag_set(
    monkeypatch: pytest.MonkeyPatch, chdir_state_tmp: Path
) -> None:
    monkeypatch.setenv("FIBREOPS_NETOPS_ROUTINE", "1")
    from fibreops import config

    config.get_settings.cache_clear()

    from fibreops.agents.factory import build_netops_coordinator_agent

    agent = build_netops_coordinator_agent()
    assert isinstance(agent, NetOpsRoutineAgent)


def test_factory_returns_chat_agent_when_routine_disabled(
    monkeypatch: pytest.MonkeyPatch, chdir_state_tmp: Path
) -> None:
    monkeypatch.delenv("FIBREOPS_NETOPS_ROUTINE", raising=False)
    from fibreops import config

    config.get_settings.cache_clear()

    from fibreops.agents.factory import build_netops_coordinator_agent, LocalAgent

    agent = build_netops_coordinator_agent(prefer="local")
    assert isinstance(agent, LocalAgent)


def test_factory_kwarg_overrides_env(
    monkeypatch: pytest.MonkeyPatch, chdir_state_tmp: Path
) -> None:
    monkeypatch.setenv("FIBREOPS_NETOPS_ROUTINE", "1")
    from fibreops import config

    config.get_settings.cache_clear()

    from fibreops.agents.factory import build_netops_coordinator_agent

    agent = build_netops_coordinator_agent(prefer="local", prefer_routine=False)
    assert not isinstance(agent, NetOpsRoutineAgent)


def test_routine_is_compatible_with_orchestrator(
    monkeypatch: pytest.MonkeyPatch, chdir_state_tmp: Path
) -> None:
    """End-to-end: orchestrator drives the routine through one critical signal."""
    monkeypatch.setenv("FIBREOPS_NETOPS_ROUTINE", "1")
    from fibreops import config

    config.get_settings.cache_clear()

    # Stub the network-touching tools at the routines module (where the
    # routine resolves them by name).
    from fibreops.tools import ticketing as _ticketing
    monkeypatch.setattr(
        _ticketing, "create_ticket", lambda **kw: {"ticket_id": "TKT-RT", "status": "new"}
    )
    from fibreops.agents import factory as _factory
    monkeypatch.setattr(
        _factory, "create_ticket", lambda **kw: {"ticket_id": "TKT-RT", "status": "new"}
    )
    monkeypatch.setattr(_factory, "post_outage_notice", lambda **kw: {"status": "logged-locally"})
    monkeypatch.setattr(_factory, "remember", lambda **kw: {"ok": True})
    monkeypatch.setattr(
        _factory,
        "dispatch_engineer",
        lambda **kw: {
            "dispatched": True,
            "engineer_name": "Priya Shah",
            "engineer_id": "ENG-1",
            "eta_minutes": 18,
            "booking": {"booking_id": "BK-RT"},
        },
    )
    monkeypatch.setattr(_factory, "update_ticket", lambda **kw: {"ok": True})
    monkeypatch.setattr(_factory, "post_status_update", lambda **kw: {"status": "logged-locally"})

    from fibreops import orchestrator
    from fibreops.models import Severity, SignalType, TelemetrySignal

    signal = TelemetrySignal(
        signal_id="sig-rt-1",
        node_id="FN-LDN-001",
        signal_type=SignalType.LOSS_OF_LIGHT,
        severity=Severity.CRITICAL,
        measured_value=-40.0,
        unit="dBm",
        raw={"last_good_dbm": -3.1},
    )
    result = asyncio.run(orchestrator.handle_signal(signal))
    steps = {s["agent"]: s for s in result["steps"]}
    assert "NetOpsCoordinatorAgent" in steps
    coord_step = steps["NetOpsCoordinatorAgent"]
    assert "HANDOFF:DISPATCH" in coord_step["decision"]
    assert coord_step["ticket"] is not None
    assert coord_step["ticket"]["ticket_id"].startswith("TKT-")
