"""Orchestrator — wires telemetry → agents → integrations.

Event loop:
  1. Subscribe to telemetry signals (Event Hub or mock generator).
  2. For each signal, run the IncidentAnalysisAgent to produce a structured
     incident analysis.
  3. Pass the analysis to the NetOpsCoordinatorAgent, which files a D365 ticket
     and posts a Teams notice.
  4. If severity warrants it (or settings.auto_dispatch is forced), invoke the
     FieldDispatchAgent.
  5. Persist a `RunRecord` to ./state/runs.jsonl so the optimiser can evaluate.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import uuid4

from .agents import (
    build_field_dispatch_agent,
    build_incident_analysis_agent,
    build_netops_coordinator_agent,
)
from .config import get_settings
from .mocks import load_json
from .models import IncidentAnalysis, Severity, TelemetrySignal
from .observability import (
    agent_span,
    get_logger,
    init_observability,
    orchestrator_span,
    record_event,
)
from .telemetry import signal_stream

logger = get_logger(__name__)

STATE_DIR = Path("state")
STATE_DIR.mkdir(exist_ok=True)
RUNS_FILE = STATE_DIR / "runs.jsonl"


def _node_context(node_id: str) -> dict[str, Any]:
    for n in load_json("fibre_nodes.json"):
        if n["node_id"] == node_id:
            return n
    return {"node_id": node_id, "region": "?", "site": "?", "customers_served": 0}


def _persist_run(record: dict[str, Any]) -> None:
    with RUNS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


async def _agent_run(agent: Any, prompt: str) -> Any:
    result = agent.run(prompt)
    if hasattr(result, "__await__"):
        return await result
    return result


def _extract_text(response: Any) -> str:
    if response is None:
        return ""
    for attr in ("text", "message", "content"):
        value = getattr(response, attr, None)
        if isinstance(value, str) and value:
            return value
    messages = getattr(response, "messages", None)
    if messages:
        last = messages[-1]
        content = getattr(last, "content", None) or getattr(last, "text", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                txt = getattr(item, "text", None) or (item.get("text") if isinstance(item, dict) else None)
                if txt:
                    parts.append(txt)
            if parts:
                return "\n".join(parts)
    return str(response)


def _parse_analysis_json(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found in agent response: {text[:200]}")
    return json.loads(text[start : end + 1])


async def handle_signal(signal: TelemetrySignal) -> dict[str, Any]:
    """Run the full agent flow for one telemetry signal."""
    run_id = f"run-{uuid4().hex[:8]}"
    node_ctx = _node_context(signal.node_id)
    incident_id = f"INC-{uuid4().hex[:8].upper()}"

    analysis_agent = build_incident_analysis_agent()
    coordinator_agent = build_netops_coordinator_agent()
    dispatch_agent = build_field_dispatch_agent()

    record: dict[str, Any] = {
        "run_id": run_id,
        "incident_id": incident_id,
        "signal": signal.model_dump(mode="json"),
        "node_context": node_ctx,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "steps": [],
    }

    # Common attributes propagated to every child span so the KQL pack works
    # against any one of them.
    common_attrs = {
        "signal_id": signal.signal_id,
        "incident_id": incident_id,
        "node_id": signal.node_id,
        "severity": signal.severity.value,
        "region": node_ctx.get("region"),
        "customers_served": node_ctx.get("customers_served", 0),
    }

    start_perf = datetime.now(timezone.utc)
    with orchestrator_span(run_id, **common_attrs) as wrap_span:
        # 1. Incident analysis
        with agent_span("IncidentAnalysisAgent", run_id, **common_attrs) as span:
            analysis_prompt = json.dumps(
                {
                    "signal_id": signal.signal_id,
                    "node_id": signal.node_id,
                    "site": node_ctx["site"],
                    "region": node_ctx["region"],
                    "customers_served": node_ctx["customers_served"],
                    "signal_type": signal.signal_type.value,
                    "severity": signal.severity.value,
                    "measured_value": signal.measured_value,
                    "unit": signal.unit,
                    "raw": signal.raw,
                }
            )
            response = await _agent_run(analysis_agent, analysis_prompt)
            text = _extract_text(response)
            analysis = _parse_analysis_json(text)
            record["steps"].append({"agent": "IncidentAnalysisAgent", "output": analysis})
            span.set_attribute("incident.severity", analysis["severity"])
            # Update common severity to the resolved (possibly escalated) value
            common_attrs["severity"] = analysis["severity"]
            wrap_span.set_attribute("severity", analysis["severity"])

        incident = IncidentAnalysis(
            signal_id=signal.signal_id,
            node_id=signal.node_id,
            severity=Severity(analysis["severity"]),
            summary=analysis["summary"],
            probable_cause=analysis["probable_cause"],
            customer_impact=analysis["customer_impact"],
            recommended_actions=analysis["recommended_actions"],
            sop_refs=analysis.get("sop_refs", []),
            incident_id=incident_id,
        )

        # 2. NetOps coordinator
        with agent_span("NetOpsCoordinatorAgent", run_id, **common_attrs) as span:
            coord_prompt = json.dumps(
                {
                    "incident_id": incident_id,
                    "node_id": signal.node_id,
                    "region": node_ctx.get("region"),
                    "site": node_ctx.get("site"),
                    "customers_served": node_ctx.get("customers_served", 0),
                    "analysis": incident.model_dump(mode="json"),
                }
            )
            coord_response = await _agent_run(coordinator_agent, coord_prompt)
            coord_text = _extract_text(coord_response)
            ticket = getattr(coord_response, "metadata", {}).get("ticket") if hasattr(coord_response, "metadata") else None
            record["steps"].append(
                {"agent": "NetOpsCoordinatorAgent", "decision": coord_text, "ticket": ticket}
            )
            decision_label = "DISPATCH" if "HANDOFF:DISPATCH" in coord_text else "MONITOR"
            span.set_attribute("decision", decision_label)
            span.set_attribute("coordinator.decision", coord_text[:80])
            should_dispatch = "HANDOFF:DISPATCH" in coord_text or (
                get_settings().auto_dispatch and incident.severity in (Severity.HIGH, Severity.CRITICAL)
            )

        wrap_span.set_attribute("decision", decision_label)
        wrap_span.set_attribute("dispatched", bool(should_dispatch and ticket))

        # 3. Field dispatch (conditional)
        if should_dispatch and ticket:
            with agent_span("FieldDispatchAgent", run_id, **common_attrs) as span:
                dispatch_prompt = json.dumps(
                    {
                        "incident_id": incident_id,
                        "ticket_id": ticket["ticket_id"],
                        "node_id": signal.node_id,
                        "signal_type": signal.signal_type.value,
                        "severity": incident.severity.value,
                    }
                )
                dispatch_response = await _agent_run(dispatch_agent, dispatch_prompt)
                dispatch_text = _extract_text(dispatch_response)
                dispatch_meta = getattr(dispatch_response, "metadata", {}) if hasattr(dispatch_response, "metadata") else {}
                record["steps"].append(
                    {"agent": "FieldDispatchAgent", "result": dispatch_text, "metadata": dispatch_meta}
                )
                span.set_attribute("dispatch.result", dispatch_text[:80])
                # SLA attribute on the wrap span: ms from signal-in to dispatch
                dispatched_at_ms = (datetime.now(timezone.utc) - start_perf).total_seconds() * 1000.0
                wrap_span.set_attribute("dispatched_at_ms", dispatched_at_ms)
                record_event(
                    "fibreops.dispatch.completed",
                    incident_id=incident_id,
                    engineer=dispatch_meta.get("dispatch", {}).get("engineer_name"),
                    eta_minutes=dispatch_meta.get("dispatch", {}).get("eta_minutes"),
                    dispatched_at_ms=dispatched_at_ms,
                )

    record["ended_at"] = datetime.now(timezone.utc).isoformat()
    _persist_run(record)
    logger.info(
        "run complete",
        extra={"run_id": run_id, "agent": "Orchestrator", "signal_id": signal.signal_id},
    )
    return record


async def run_loop(*, max_signals: int | None = None) -> list[dict[str, Any]]:
    init_observability()
    results: list[dict[str, Any]] = []
    count = 0
    async for sig in signal_stream():
        result = await handle_signal(sig)
        results.append(result)
        count += 1
        if max_signals and count >= max_signals:
            break
    return results


async def stream_signals() -> AsyncIterator[TelemetrySignal]:
    async for sig in signal_stream():
        yield sig
