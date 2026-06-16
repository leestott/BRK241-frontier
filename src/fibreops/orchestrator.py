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

import asyncio
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
from .tools import (
    create_ticket as _create_ticket,
    dispatch_engineer as _dispatch_engineer,
    lookup_sop as _lookup_sop,
    post_outage_notice as _post_outage_notice,
    post_status_update as _post_status_update,
    update_ticket as _update_ticket,
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
    if start == -1:
        raise ValueError(f"No JSON object found in agent response: {text[:200]}")
    try:
        # raw_decode stops at the first complete JSON object, ignoring trailing text
        obj, _ = json.JSONDecoder().raw_decode(text, start)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Failed to parse agent JSON response: {exc} | text={text[:300]}") from exc
    return _normalise_analysis(obj)


_VALID_SEVERITIES = {"low", "medium", "high", "critical"}

def _normalise_analysis(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalise LLM output to the expected lowercase snake_case schema.

    Hosted agents sometimes capitalise keys or use alternative names.
    This tries case-insensitive lookups and falls back gracefully.
    """
    # Build a lowercase key → original value lookup for fuzzy matching
    lc: dict[str, Any] = {k.lower().replace(" ", "_"): v for k, v in raw.items()}

    def _get(*candidates: str, default: Any = None) -> Any:
        for c in candidates:
            if c in raw:
                return raw[c]
            if c.lower() in lc:
                return lc[c.lower()]
            lc_c = c.lower().replace(" ", "_")
            if lc_c in lc:
                return lc[lc_c]
        return default

    severity_raw = str(_get("severity", "Severity", "SEVERITY", "severity_level", "risk_level", default="medium")).lower()
    severity = severity_raw if severity_raw in _VALID_SEVERITIES else "medium"

    actions_raw = _get("recommended_actions", "Recommended Actions", "recommended actions", "actions", "next_actions", default=[])
    if isinstance(actions_raw, str):
        actions_raw = [actions_raw]

    return {
        "summary": str(_get("summary", "Summary", "SUMMARY", "title", "description", default="Incident detected")),
        "probable_cause": str(_get("probable_cause", "probable cause", "Probable Cause", "root_cause", "cause", default="Unknown")),
        "customer_impact": str(_get("customer_impact", "customer impact", "Customer Impact", "impact", default="Unknown")),
        "severity": severity,
        "recommended_actions": actions_raw if isinstance(actions_raw, list) else list(actions_raw),
        "sop_refs": list(_get("sop_refs", "SOP Refs", "sop references", "references", default=[]) or []),
        "knowledge": _get("knowledge", "Knowledge", default={}),
    }


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
            if ticket is None:
                # Hosted/Foundry agents execute create_ticket as a tool side-effect;
                # the result isn't in metadata, so fall back to the D365 state file.
                try:
                    d365_path = Path("state/d365_store.json")
                    if d365_path.exists():
                        d365 = json.loads(d365_path.read_text(encoding="utf-8"))
                        ticket = d365.get("incidents", {}).get(incident_id)
                except Exception:
                    pass
            if ticket is None:
                # Final fallback: hosted/foundry agents may only generate text
                # describing tool calls rather than executing them. Construct
                # the ticket directly from known incident data so field_dispatch
                # can proceed, and best-effort persist to D365 for tracking.
                ticket = {
                    "ticket_id": incident_id,
                    "incident_id": incident_id,
                    "severity": incident.severity.value,
                    "node_id": signal.node_id,
                    "status": "new",
                    "assignee": None,
                    "title": f"[{incident.severity.value.upper()}] {incident.summary[:80]}",
                }
                try:
                    d365_ticket = await asyncio.to_thread(
                        _create_ticket,
                        incident_id=incident_id,
                        node_id=signal.node_id,
                        severity=incident.severity.value,
                        title=ticket["title"],
                        description=(
                            f"{incident.probable_cause}\n\n"
                            f"Impact: {incident.customer_impact}\n"
                            f"Recommended: {'; '.join(incident.recommended_actions)}"
                        ),
                    )
                    ticket = d365_ticket  # use richer D365 record if available
                    await asyncio.to_thread(
                        _post_outage_notice,
                        incident_id=incident_id,
                        node_id=signal.node_id,
                        severity=incident.severity.value,
                        summary=incident.summary,
                        customer_impact=incident.customer_impact,
                        probable_cause=incident.probable_cause,
                    )
                except Exception as exc:
                    logger.warning("Orchestrator D365/Teams fallback failed: %s", exc)
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
                if not dispatch_meta.get("dispatch", {}).get("dispatched"):
                    # Hosted/Foundry agents may not execute dispatch tools; run
                    # them directly so engineer dispatch and ticket update happen.
                    try:
                        sop_signal_type = signal.signal_type.value
                        sop = await asyncio.to_thread(_lookup_sop, signal_type=sop_signal_type)
                        sop_text = sop.get("text", "") if isinstance(sop, dict) else str(sop)
                        import re as _re
                        skills: list[str] = []
                        if _re.search(r"splicing", sop_text, _re.I):
                            skills.append("splicing")
                        if _re.search(r"OTDR", sop_text):
                            skills.append("OTDR")
                        dispatch_result = await asyncio.to_thread(
                            _dispatch_engineer,
                            incident_id=incident_id,
                            node_id=signal.node_id,
                            required_skills=skills or ["splicing"],
                        )
                        if dispatch_result.get("dispatched"):
                            await asyncio.to_thread(
                                _post_status_update,
                                incident_id=incident_id,
                                status="engineer_dispatched",
                                note=f"Engineer en route to {signal.node_id}",
                                engineer_name=dispatch_result.get("engineer_name"),
                                eta_minutes=dispatch_result.get("eta_minutes"),
                            )
                            await asyncio.to_thread(
                                _update_ticket,
                                ticket_id=ticket["ticket_id"],
                                status="assigned",
                                assignee=dispatch_result.get("engineer_name"),
                            )
                        dispatch_meta = {"dispatch": dispatch_result}
                        dispatch_text = (
                            f"DISPATCHED {dispatch_result.get('engineer_name')} "
                            f"ETA {dispatch_result.get('eta_minutes')} min"
                            if dispatch_result.get("dispatched")
                            else f"NO_ENGINEER_AVAILABLE {dispatch_result.get('reason','unknown')}"
                        )
                    except Exception as exc:
                        logger.warning("Orchestrator fallback dispatch failed: %s", exc)
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
