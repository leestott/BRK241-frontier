"""Foundry Agent Service **Routines** — deterministic, auditable agent plans.

The BRK241 deck (slide 11) announces *Routines in Foundry Agent Service*. A
Routine is a stored, declarative sequence of steps the runtime executes — the
agent author writes the plan once, the platform runs it the same way every
time. That's a much better fit for the **NetOps coordinator** role than an
open-ended chat agent: the coordinator's job is exactly three deterministic
steps (file ticket, post Teams notice, decide handoff) and we want the same
behaviour every time.

This module provides:

* :class:`NetOpsRoutineAgent` — a lightweight runner that executes the
  documented plan against the in-process tools. Honours the same
  ``await agent.run(prompt) -> response`` contract every other backend uses,
  so the orchestrator code is unchanged.
* :data:`NETOPS_ROUTINE_DEFINITION` — the declarative plan, exposed for the
  publisher / docs / tests / a future SDK-backed publish path.

When the Foundry SDK exposes a public ``Routine`` primitive, the publisher can
lift :data:`NETOPS_ROUTINE_DEFINITION` into a hosted Routine via
``AIProjectClient.routines.create_version(...)``. Until then the local runner
gives the same observable behaviour and the same trace shape.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from ..observability import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class RoutineStep:
    """One step in a Routine plan.

    The ``tool`` name is symbolic — the runner resolves it against the
    ``tool_fns`` dict supplied at construction time. ``inputs`` is a Jinja-ish
    template mapping where ``{var}`` placeholders are resolved from prior step
    outputs + the original payload. ``capture_as`` (if set) records the tool
    output under that key so later steps can reference it.
    """

    name: str
    tool: str
    inputs: dict[str, str]
    capture_as: str | None = None
    when: str | None = None  # optional expression e.g. "severity in ['high','critical']"


@dataclass
class RoutineDefinition:
    name: str
    description: str
    steps: list[RoutineStep]
    decision: dict[str, str] = field(default_factory=dict)
    """Maps a literal decision label (e.g. ``HANDOFF:DISPATCH``) to an
    expression that must evaluate truthy for the runner to pick it."""


NETOPS_ROUTINE_DEFINITION = RoutineDefinition(
    name="netops-coordinator-v1",
    description=(
        "Deterministic 3-step plan: file the D365 ticket, publish the Teams "
        "outage notice, decide whether to hand off to Field Dispatch."
    ),
    steps=[
        RoutineStep(
            name="file_ticket",
            tool="create_ticket",
            inputs={
                "incident_id": "{incident_id}",
                "node_id": "{node_id}",
                "severity": "{analysis.severity}",
                "title": "[{analysis.severity_upper}] {analysis.summary}",
                "description": (
                    "{analysis.probable_cause}\n\n"
                    "Impact: {analysis.customer_impact}\n"
                    "Recommended: {analysis.actions_joined}"
                ),
            },
            capture_as="ticket",
        ),
        RoutineStep(
            name="post_teams_notice",
            tool="post_outage_notice",
            inputs={
                "incident_id": "{incident_id}",
                "node_id": "{node_id}",
                "severity": "{analysis.severity}",
                "summary": "{analysis.summary}",
                "customer_impact": "{analysis.customer_impact}",
                "probable_cause": "{analysis.probable_cause}",
            },
        ),
        RoutineStep(
            name="remember_ticket",
            tool="remember",
            inputs={
                "scope": "global",
                "key": "last_ticket_for_node:{node_id}",
                "value": "{ticket_memory}",
            },
        ),
    ],
    decision={
        "HANDOFF:DISPATCH": "severity in ('high','critical')",
        "MONITOR": "severity not in ('high','critical')",
    },
)


@dataclass
class RoutineResponse:
    text: str
    metadata: dict[str, Any]


def _resolve_value(template: str, ctx: dict[str, Any]) -> Any:
    """Resolve a single ``{path.to.value}`` placeholder.

    If ``template`` is a single placeholder, return the raw value (preserving
    type — important for dicts passed to ``remember``). Otherwise format as a
    string with all placeholders expanded.
    """
    stripped = template.strip()
    if stripped.startswith("{") and stripped.endswith("}") and stripped.count("{") == 1:
        path = stripped[1:-1]
        return _lookup(path, ctx)
    out = template
    start = 0
    while True:
        i = out.find("{", start)
        if i == -1:
            return out
        j = out.find("}", i + 1)
        if j == -1:
            return out
        path = out[i + 1 : j]
        value = _lookup(path, ctx)
        out = out[:i] + ("" if value is None else str(value)) + out[j + 1 :]
        start = i + len(str(value) if value is not None else "")


def _lookup(path: str, ctx: dict[str, Any]) -> Any:
    cur: Any = ctx
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            cur = getattr(cur, part, None)
        if cur is None:
            return None
    return cur


def _eval_decision(expr: str, ctx: dict[str, Any]) -> bool:
    """Evaluate a tiny, safe boolean expression like ``severity in ('high',)``."""
    safe_globals: dict[str, Any] = {"__builtins__": {}}
    safe_locals = {k: ctx.get(k) for k in ("severity", "incident_id", "node_id")}
    try:
        return bool(eval(expr, safe_globals, safe_locals))  # noqa: S307
    except Exception:
        logger.warning("routine decision expression failed: %r", expr)
        return False


class NetOpsRoutineAgent:
    """Local executor of :data:`NETOPS_ROUTINE_DEFINITION`.

    Honours the same ``await agent.run(prompt)`` contract as ``LocalAgent`` /
    ``FoundryAgent`` so the orchestrator never needs to know which backend is
    actually doing the work.
    """

    name = "NetOpsCoordinatorAgent"
    role = "netops_coordinator"
    definition = NETOPS_ROUTINE_DEFINITION

    def __init__(self, tools: dict[str, Callable[..., Any]]):
        self.tools = tools

    async def run(self, prompt: str, **_: Any) -> RoutineResponse:
        payload = json.loads(prompt) if prompt.lstrip().startswith("{") else {}
        analysis = payload.get("analysis", {})
        ctx: dict[str, Any] = {
            "incident_id": payload.get("incident_id"),
            "node_id": payload.get("node_id"),
            "severity": analysis.get("severity"),
            "analysis": {
                **analysis,
                "severity_upper": (analysis.get("severity") or "").upper(),
                "actions_joined": "; ".join(analysis.get("recommended_actions", [])),
            },
        }
        step_log: list[dict[str, Any]] = []
        for step in self.definition.steps:
            if step.when and not _eval_decision(step.when, ctx):
                step_log.append({"step": step.name, "status": "skipped", "reason": step.when})
                continue
            kwargs = {k: _resolve_value(v, ctx) for k, v in step.inputs.items()}
            # Special-case the captured ticket-id for the remember step so the
            # caller doesn't have to string-template a dict.
            if step.tool == "remember" and step.inputs.get("value") == "{ticket_memory}":
                ticket = ctx.get("ticket") or {}
                kwargs["value"] = {
                    "ticket_id": ticket.get("ticket_id"),
                    "severity": ctx.get("severity"),
                }
            tool = self.tools.get(step.tool)
            if tool is None:
                raise RuntimeError(
                    f"Routine step '{step.name}' requires tool '{step.tool}' but it was not provided"
                )
            output = tool(**kwargs)
            if step.capture_as:
                ctx[step.capture_as] = output
            step_log.append(
                {"step": step.name, "status": "ok", "tool": step.tool, "output": output}
            )

        decision_label = "MONITOR"
        for label, expr in self.definition.decision.items():
            if _eval_decision(expr, ctx):
                decision_label = label
                break

        reason = (
            "auto-dispatch threshold met"
            if decision_label.startswith("HANDOFF")
            else "below dispatch threshold"
        )
        ticket = ctx.get("ticket")
        return RoutineResponse(
            text=f"{decision_label} {reason}",
            metadata={
                "ticket": ticket,
                "decision": decision_label,
                "routine": {
                    "name": self.definition.name,
                    "steps": step_log,
                },
            },
        )
