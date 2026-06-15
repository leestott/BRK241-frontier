"""Field engineer dispatch tool.

Selection algorithm (transparent and easy to demo):
  1. Filter to engineers in the affected node's region.
  2. Require any of the requested skills (default: 'splicing').
  3. Must be on shift.
  4. Sort by distance to node ascending, then return the closest.
  5. ETA = max(15, round(distance_km * 3))  minutes (urban traffic heuristic).
"""
from __future__ import annotations

from typing import Any

from ..mocks import load_json
from ..observability import get_logger, tool_span
from .ticketing import create_booking

logger = get_logger(__name__)


def _engineers() -> list[dict[str, Any]]:
    return load_json("engineers.json")


def _node(node_id: str) -> dict[str, Any]:
    for n in load_json("fibre_nodes.json"):
        if n["node_id"] == node_id:
            return n
    return {}


def find_best_engineer(node_id: str, required_skills: list[str] | None = None) -> dict[str, Any]:
    """Return the best engineer to send to a node, with a scoring breakdown."""
    with tool_span("dispatch.find_best_engineer", node_id=node_id):
        skills = required_skills or ["splicing"]
        node = _node(node_id)
        region = node.get("region")
        candidates: list[tuple[float, dict[str, Any]]] = []
        for eng in _engineers():
            if eng["region"] != region:
                continue
            if not eng["on_shift"]:
                continue
            if not any(s in eng["skills"] for s in skills):
                continue
            distance = eng.get("distance_km_from_node", {}).get(node_id, 999.0)
            candidates.append((distance, eng))
        if not candidates:
            logger.warning("no candidate engineer for node %s", node_id)
            return {"engineer": None, "reason": "no in-region, on-shift, skilled engineer"}
        candidates.sort(key=lambda x: x[0])
        distance, best = candidates[0]
        eta = max(15, round(distance * 3))
        return {
            "engineer": best,
            "eta_minutes": eta,
            "distance_km": distance,
            "candidates_considered": len(candidates),
        }


def dispatch_engineer(*, incident_id: str, node_id: str, required_skills: list[str] | None = None) -> dict[str, Any]:
    """Find the best engineer and book them via D365 Field Service."""
    with tool_span("dispatch.dispatch_engineer", incident_id=incident_id, node_id=node_id):
        choice = find_best_engineer(node_id, required_skills)
        eng = choice.get("engineer")
        if not eng:
            return {"dispatched": False, **choice}
        booking = create_booking(
            incident_id=incident_id,
            engineer_id=eng["engineer_id"],
            engineer_name=eng["name"],
            eta_minutes=choice["eta_minutes"],
            notes=f"Auto-dispatched by FibreOps Field Dispatch agent. Distance {choice['distance_km']:.1f} km.",
        )
        return {
            "dispatched": True,
            "engineer_id": eng["engineer_id"],
            "engineer_name": eng["name"],
            "eta_minutes": choice["eta_minutes"],
            "booking": booking,
        }
