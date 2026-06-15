"""Domain model invariants. Lightweight but catches breaking changes early."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from fibreops.models import (
    DispatchResult,
    Engineer,
    FibreNode,
    IncidentAnalysis,
    Severity,
    SignalType,
    TelemetrySignal,
    Ticket,
)


def test_severity_ordering_via_enum_values():
    # The optimiser indexes into this list — if the values change, the
    # optimiser's severity_consistency check silently breaks.
    order = [Severity.LOW.value, Severity.MEDIUM.value, Severity.HIGH.value, Severity.CRITICAL.value]
    assert order == ["low", "medium", "high", "critical"]


def test_signal_type_values_cover_supported_signals():
    assert {s.value for s in SignalType} == {
        "loss_of_light",
        "high_attenuation",
        "ber_degradation",
        "node_unreachable",
    }


def test_telemetry_signal_auto_populates_id_and_timestamp():
    sig = TelemetrySignal(
        node_id="FN-LDN-001",
        signal_type=SignalType.LOSS_OF_LIGHT,
        severity=Severity.CRITICAL,
        measured_value=-40.0,
        unit="dBm",
    )
    assert sig.signal_id.startswith("sig-") and len(sig.signal_id) > 5
    assert sig.timestamp.tzinfo == timezone.utc
    assert isinstance(sig.timestamp, datetime)


def test_telemetry_signal_rejects_unknown_severity():
    with pytest.raises(ValidationError):
        TelemetrySignal(
            node_id="FN-LDN-001",
            signal_type=SignalType.LOSS_OF_LIGHT,
            severity="catastrophic",  # type: ignore[arg-type]
            measured_value=-40.0,
            unit="dBm",
        )


def test_fibre_node_defaults():
    node = FibreNode(node_id="FN-X", region="London", site="Test")
    assert node.criticality == Severity.MEDIUM
    assert node.customers_served == 0
    assert node.upstream is None


def test_engineer_defaults_on_shift_true():
    eng = Engineer(engineer_id="ENG-1", name="A", region="London", skills=["splicing"])
    assert eng.on_shift is True
    assert eng.distance_km_from_node == {}


def test_incident_analysis_auto_generates_id():
    inc = IncidentAnalysis(
        signal_id="sig-1",
        node_id="FN-LDN-001",
        severity=Severity.HIGH,
        summary="s",
        probable_cause="pc",
        customer_impact="ci",
        recommended_actions=["a"],
    )
    assert inc.incident_id.startswith("INC-") and inc.incident_id == inc.incident_id.upper()
    assert inc.sop_refs == []


def test_ticket_status_literal_rejects_unknown():
    with pytest.raises(ValidationError):
        Ticket(
            ticket_id="T1",
            incident_id="I1",
            node_id="N1",
            severity=Severity.HIGH,
            title="t",
            description="d",
            status="exploded",  # type: ignore[arg-type]
        )


def test_dispatch_result_round_trip():
    r = DispatchResult(
        engineer_id="ENG-1",
        engineer_name="A",
        eta_minutes=18,
        ticket_id="TKT-1",
        notes="auto",
    )
    assert r.model_dump()["eta_minutes"] == 18
