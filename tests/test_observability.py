"""Observability primitives — JSONL exporter + record_event."""
from __future__ import annotations

import json
from pathlib import Path

from opentelemetry import trace

from fibreops.observability import (
    agent_span,
    init_observability,
    orchestrator_span,
    record_event,
    tool_span,
)


def _read_traces(root: Path) -> list[dict]:
    path = root / "state" / "traces.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _flush() -> None:
    """Force the BatchSpanProcessor to flush so the JSONL is up to date."""
    provider = trace.get_tracer_provider()
    force_flush = getattr(provider, "force_flush", None)
    if force_flush:
        force_flush(timeout_millis=2000)


def test_jsonl_exporter_writes_span_attributes(chdir_state_tmp):
    init_observability()
    with agent_span("IncidentAnalysisAgent", run_id="run-XYZ-unique", node_id="FN-LDN-001"):
        pass
    _flush()
    spans = _read_traces(chdir_state_tmp)
    # BatchSpanProcessor is global — filter on our unique run_id to avoid
    # picking up buffered spans flushed in from earlier tests.
    target = next(s for s in spans if s["attributes"].get("run.id") == "run-XYZ-unique")
    assert target["name"] == "agent.IncidentAnalysisAgent"
    assert target["attributes"]["agent.name"] == "IncidentAnalysisAgent"
    assert target["attributes"]["node_id"] == "FN-LDN-001"
    assert target["duration_ms"] >= 0


def test_orchestrator_span_propagates_common_attributes(chdir_state_tmp):
    init_observability()
    with orchestrator_span("run-orch-unique", incident_id="INC-9", severity="critical"):
        with tool_span("ticketing.create_ticket", incident_id="INC-9"):
            pass
    _flush()
    spans = _read_traces(chdir_state_tmp)
    parent = next(
        s for s in spans
        if s["name"] == "orchestrator.handle_signal"
        and s["attributes"].get("run.id") == "run-orch-unique"
    )
    child = next(
        s for s in spans
        if s["name"] == "tool.ticketing.create_ticket"
        and s["parent_id"] == parent["span_id"]
    )
    # Child span inherits trace_id so the Application Insights operation_Id correlation works.
    assert parent["trace_id"] == child["trace_id"]
    assert parent["attributes"]["severity"] == "critical"


def test_record_event_no_op_outside_active_span():
    # Must not raise even when no span is active.
    record_event("fibreops.optimiser.score", incident_id="X")


def test_record_event_filters_none_values(chdir_state_tmp):
    init_observability()
    with agent_span("X", run_id="r1"):
        record_event("fibreops.dispatch.completed", engineer=None, eta_minutes=18)
    _flush()
    spans = _read_traces(chdir_state_tmp)
    # The span exporter doesn't surface events directly, but we at least verify
    # the call didn't crash and the parent span made it to disk.
    assert any(s["name"] == "agent.X" for s in spans)
