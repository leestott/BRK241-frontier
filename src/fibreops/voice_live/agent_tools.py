"""FibreOps tool surface for the Foundry voice agent.

Provides:
  * ``INSTRUCTIONS`` and ``TOOL_DEFINITIONS`` — shared publisher definition.
  * ``dispatch`` — coroutine that executes a tool call and returns the
    JSON-serialisable string the model expects in ``function_call_output``.

The proxy handles function calls advertised on the published voice agent.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..observability import get_logger

logger = get_logger(__name__)


_DEFINITION = json.loads((Path(__file__).with_name("definition.json")).read_text(encoding="utf-8"))
INSTRUCTIONS: str = _DEFINITION["instructions"]
TOOL_DEFINITIONS: list[dict[str, Any]] = _DEFINITION["tools"]


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
