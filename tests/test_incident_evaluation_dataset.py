import json

from scripts.build_incident_evaluation_dataset import build_rows


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
