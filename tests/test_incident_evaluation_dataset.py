import json

from scripts.build_incident_evaluation_dataset import build_rows, build_technical_rows


def test_build_rows_preserves_recorded_outputs_and_context():
    run = {
        "run_id": "run-1",
        "incident_id": "INC-1",
        "signal": {
            "signal_id": "sig-private-runtime-id",
            "timestamp": "2026-10-11T12:00:00Z",
            "node_id": "FN-DXB-009",
            "signal_type": "loss_of_light",
            "severity": "critical",
            "measured_value": -40,
            "unit": "dBm",
        },
        "node_context": {
            "site": "Dubai Internet City Exchange",
            "customers_served": 9500,
        },
        "steps": [
            {
                "agent": "IncidentAnalysisAgent",
                "output": {
                    "summary": "Recorded output",
                    "severity": "critical",
                },
            },
            {
                "agent": "NetOpsCoordinatorAgent",
                "decision": (
                    "HANDOFF:DISPATCH created_at=2026-10-11T12:01:00Z "
                    "run=run-private signal=sig-private booking=BK-private"
                ),
                "ticket": {
                    "incident_id": "INC-1",
                    "created_at": "ignored",
                    "events": [{"event": "created"}],
                },
            },
            {
                "agent": "FieldDispatchAgent",
                "result": "DISPATCHED",
                "metadata": {
                    "dispatch": {
                        "booking": {
                            "booking_id": "BK-private-runtime-id",
                            "created_at": "2026-10-11T12:01:00Z",
                            "engineer_name": "Amina Al Mansoori",
                        },
                        "eta_minutes": 15,
                    }
                },
            },
        ],
    }

    rows = build_rows([run])

    assert len(rows) == 3
    assert json.loads(rows[0]["response"])["summary"] == "Recorded output"
    context = json.loads(rows[0]["context"])
    assert context["signal"]["severity"] == "critical"
    assert "run_id" not in context
    assert "signal_id" not in context["signal"]
    assert "timestamp" not in context["signal"]
    coordinator_response = json.loads(rows[1]["response"])
    assert coordinator_response["ticket"] == {"incident_id": "INC-1"}
    assert coordinator_response["decision"] == (
        "HANDOFF:DISPATCH created_at=<timestamp> "
        "run=<run-id> signal=<signal-id> booking=<booking-id>"
    )
    dispatch_response = json.loads(rows[2]["response"])
    assert dispatch_response["result"] == "DISPATCHED"
    assert dispatch_response["metadata"]["dispatch"]["booking"] == {
        "engineer_name": "Amina Al Mansoori"
    }


def test_build_rows_does_not_generate_rows_for_unknown_steps():
    run = {
        "run_id": "run-1",
        "incident_id": "INC-1",
        "signal": {},
        "node_context": {},
        "steps": [{"agent": "UnknownAgent", "output": "ignored"}],
    }

    assert build_rows([run]) == []


def test_build_technical_rows_requires_exact_signal_and_dispatch_facts():
    run = {
        "run_id": "run-private",
        "incident_id": "INC-TECH-1",
        "signal": {
            "signal_id": "sig-private",
            "timestamp": "2026-10-11T12:00:00Z",
            "node_id": "FN-LDN-001",
            "signal_type": "loss_of_light",
            "severity": "critical",
            "measured_value": -40,
            "unit": "dBm",
            "raw": {
                "consecutive_polls_dark": 6,
                "last_good_dbm": -18.4,
                "olt_port": "1/1/3",
            },
        },
        "node_context": {
            "site": "Shoreditch CO",
            "customers_served": 12400,
        },
        "steps": [
            {
                "agent": "IncidentAnalysisAgent",
                "output": {"severity": "critical", "summary": "Loss of light"},
            },
            {
                "agent": "NetOpsCoordinatorAgent",
                "decision": "DISPATCH",
                "ticket": {
                    "incident_id": "INC-TECH-1",
                    "node_id": "FN-LDN-001",
                    "severity": "critical",
                },
            },
            {
                "agent": "FieldDispatchAgent",
                "result": "DISPATCHED",
                "metadata": {"dispatch": {"eta_minutes": 15}},
            },
        ],
    }

    rows = build_technical_rows([run])

    assert len(rows) == 1
    context = json.loads(rows[0]["context"])
    response = json.loads(rows[0]["response"])
    requirements = json.loads(rows[0]["ground_truth"])["requirements"]
    assert "run_id" not in context
    assert "signal_id" not in context["signal"]
    assert response["decision"] == "DISPATCH"
    assert response["dispatch"]["eta_minutes"] == 15
    assert any("-40 dBm" in item for item in requirements)
    assert any("6 consecutive dark polls" in item for item in requirements)
    assert any("-18.4 dBm" in item for item in requirements)
    assert any("OLT port 1/1/3" in item for item in requirements)
