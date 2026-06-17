"""FibreOps tool surface for the Voice Live realtime agent.

Provides:
  * ``INSTRUCTIONS`` — system prompt that turns the realtime model into a
    FibreOps NOC copilot.
  * ``TOOL_DEFINITIONS`` — OpenAI realtime ``tools`` schema (function tools)
    advertised to the upstream model in the ``session.update`` payload.
  * ``dispatch`` — coroutine that executes a tool call and returns the
    JSON-serialisable string the model expects in ``function_call_output``.

The proxy in ``voice_live/__init__.py`` is responsible for:
  1. Injecting these into the first ``session.update`` it forwards upstream.
  2. Watching for ``response.function_call_arguments.done`` events from
     upstream, calling :func:`dispatch`, and replying with
     ``conversation.item.create`` (``function_call_output``) +
     ``response.create``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..observability import get_logger

logger = get_logger(__name__)


INSTRUCTIONS = (
    "You are FibreOps NOC Copilot — a calm, concise voice assistant for "
    "network operations engineers responding to fibre outages. You speak "
    "in a polite British English style, keep replies short (under 30 "
    "seconds of speech), and use the tools provided to look up real "
    "incident, node, and SOP data instead of guessing. When asked about "
    "the latest incidents, call list_recent_incidents. When given a "
    "specific incident or run id (e.g. 'INC-1042' or 'run_8f3...'), call "
    "lookup_incident. When asked about a node (e.g. 'FN-204'), call "
    "lookup_node. When the engineer wants the playbook for a signal "
    "type, call lookup_sop. When asked to notify the team or escalate, "
    "call notify_teams. After a tool returns, summarise the result in "
    "natural speech — never read raw JSON aloud."
)


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "list_recent_incidents",
        "description": (
            "List the most recent FibreOps incidents (run records) ordered "
            "newest first. Use this when the engineer asks 'what's "
            "happening', 'any active outages', 'show recent incidents'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of incidents to return (1-10).",
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "lookup_incident",
        "description": (
            "Return full detail for a single incident by incident id "
            "(e.g. INC-1042) or run id."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "Incident id (INC-…) or run id.",
                },
            },
            "required": ["id"],
        },
    },
    {
        "type": "function",
        "name": "lookup_node",
        "description": (
            "Return topology metadata for a fibre node (region, site, "
            "customers served, parent links) given its node id, e.g. FN-204."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "string",
                    "description": "Fibre node id, e.g. FN-204.",
                },
            },
            "required": ["node_id"],
        },
    },
    {
        "type": "function",
        "name": "lookup_sop",
        "description": (
            "Return the standard operating procedure for a signal type. "
            "Valid signal types: loss_of_light, node_unreachable, "
            "high_attenuation, ber_degradation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "signal_type": {
                    "type": "string",
                    "description": "Signal type keyword.",
                },
            },
            "required": ["signal_type"],
        },
    },
    {
        "type": "function",
        "name": "notify_teams",
        "description": (
            "Post a status update to the NOC Microsoft Teams channel. "
            "Use only when the engineer explicitly asks to notify, "
            "escalate, or post an update. The post is queued to the local "
            "outbox when no webhook is configured."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "incident_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "description": "Short status label, e.g. 'Engineer dispatched'.",
                },
                "note": {
                    "type": "string",
                    "description": "Free-text note for the update body.",
                },
            },
            "required": ["incident_id", "status", "note"],
        },
    },
]


_RUNS_PATH = Path("state") / "runs.jsonl"


def _load_runs(limit: int = 50) -> list[dict[str, Any]]:
    if not _RUNS_PATH.exists():
        return []
    rows: list[dict[str, Any]] = []
    with _RUNS_PATH.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    rows.sort(key=lambda r: r.get("started_at", ""), reverse=True)
    return rows[:limit]


def _summarise_run(run: dict[str, Any]) -> dict[str, Any]:
    sig = run.get("signal", {})
    ctx = run.get("node_context", {})
    steps = {s.get("agent"): s for s in run.get("steps", [])}
    analysis = steps.get("IncidentAnalysisAgent", {}).get("output", {})
    coord = steps.get("NetOpsCoordinatorAgent", {})
    dispatch = steps.get("FieldDispatchAgent", {})
    dispatched = bool(dispatch and "DISPATCHED" in str(dispatch.get("result", "")))
    meta = dispatch.get("metadata", {}).get("dispatch", {}) if dispatched else {}
    return {
        "incident_id": run.get("incident_id"),
        "run_id": run.get("run_id"),
        "started_at": run.get("started_at"),
        "node_id": sig.get("node_id"),
        "region": ctx.get("region"),
        "site": ctx.get("site"),
        "customers_affected": ctx.get("customers_served", 0),
        "signal_type": sig.get("signal_type"),
        "severity": analysis.get("severity") or sig.get("severity", "low"),
        "summary": analysis.get("summary", ""),
        "ticket_id": (coord.get("ticket") or {}).get("id") if coord else None,
        "dispatched": dispatched,
        "engineer": meta.get("engineer_name"),
        "eta_minutes": meta.get("eta_minutes"),
    }


async def _list_recent_incidents(limit: int = 5) -> dict[str, Any]:
    limit = max(1, min(int(limit or 5), 10))
    runs = _load_runs(limit=limit)
    return {"count": len(runs), "incidents": [_summarise_run(r) for r in runs]}


async def _lookup_incident(id: str) -> dict[str, Any]:
    needle = (id or "").strip()
    if not needle:
        return {"error": "Missing incident id."}
    for run in _load_runs(limit=500):
        if run.get("incident_id") == needle or run.get("run_id") == needle:
            return _summarise_run(run)
    return {"error": f"No incident found for id '{needle}'."}


async def _lookup_node(node_id: str) -> dict[str, Any]:
    from ..tools.knowledge import lookup_node

    nid = (node_id or "").strip()
    if not nid:
        return {"error": "Missing node_id."}
    node = lookup_node(nid)
    if not node:
        return {"error": f"No node found for '{nid}'."}
    return node


async def _lookup_sop(signal_type: str) -> dict[str, Any]:
    from ..tools.knowledge import lookup_sop

    return lookup_sop((signal_type or "").strip().lower())


async def _notify_teams(incident_id: str, status: str, note: str) -> dict[str, Any]:
    from ..tools.teams import post_status_update

    try:
        return post_status_update(incident_id=incident_id, status=status, note=note)
    except Exception as exc:
        logger.warning("notify_teams failed: %s", exc)
        return {"error": str(exc)}


_DISPATCH = {
    "list_recent_incidents": _list_recent_incidents,
    "lookup_incident": _lookup_incident,
    "lookup_node": _lookup_node,
    "lookup_sop": _lookup_sop,
    "notify_teams": _notify_teams,
}


async def dispatch(name: str, arguments_json: str) -> str:
    """Execute a function call and return a JSON string for function_call_output."""
    handler = _DISPATCH.get(name)
    if handler is None:
        return json.dumps({"error": f"Unknown tool '{name}'."})
    try:
        args = json.loads(arguments_json) if arguments_json else {}
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"Invalid arguments JSON: {exc}"})
    if not isinstance(args, dict):
        return json.dumps({"error": "Arguments must be an object."})
    try:
        result = await handler(**args)
    except TypeError as exc:
        return json.dumps({"error": f"Bad arguments: {exc}"})
    except Exception as exc:
        logger.warning("Tool %s raised: %s", name, exc)
        return json.dumps({"error": str(exc)})
    try:
        return json.dumps(result, default=str)
    except (TypeError, ValueError):
        return json.dumps({"error": "Tool returned non-serialisable data."})
