"""Agent optimiser.

Reads ./state/runs.jsonl + ./state/traces.jsonl, scores each run against a
deterministic rubric, and emits improvement suggestions for the prompt /
tooling. When ``FIBREOPS_FOUNDRY_EVALS`` is set (and a Foundry project endpoint
is configured) it additionally runs the cloud Evaluators
(`agent_framework_foundry.evaluate_traces`) over recent agent traces and folds
the metrics into the summary — otherwise the local rubric is authoritative, so
the optimiser stays fully offline by default.

Every scored run also emits OpenTelemetry span events
(``fibreops.optimiser.score``, ``fibreops.optimiser.criterion``) so the
KQL pack in ``docs/KQL.md`` can chart score trends over time in App
Insights.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import get_settings
from .observability import get_logger, get_tracer, record_event

logger = get_logger(__name__)

STATE_DIR = Path("state")
RUNS_FILE = STATE_DIR / "runs.jsonl"
TRACE_FILE = STATE_DIR / "traces.jsonl"
SUGGESTIONS_FILE = STATE_DIR / "optimiser_suggestions.jsonl"


def _load_runs() -> list[dict[str, Any]]:
    if not RUNS_FILE.exists():
        return []
    return [json.loads(line) for line in RUNS_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]


def _load_traces() -> list[dict[str, Any]]:
    if not TRACE_FILE.exists():
        return []
    return [json.loads(line) for line in TRACE_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]


def score_run(run: dict[str, Any]) -> dict[str, Any]:
    """Rubric-based scoring. Each criterion scores 0..1; total is the mean."""
    steps = {step["agent"]: step for step in run.get("steps", [])}
    analysis = steps.get("IncidentAnalysisAgent", {}).get("output", {})
    coord = steps.get("NetOpsCoordinatorAgent", {})
    dispatch = steps.get("FieldDispatchAgent", {})

    criteria: dict[str, float] = {}
    reasons: list[str] = []

    # 1. Analysis structural completeness
    required = {"summary", "probable_cause", "customer_impact", "severity", "recommended_actions", "sop_refs"}
    have = required.intersection(analysis or {})
    criteria["analysis_completeness"] = len(have) / len(required)
    if criteria["analysis_completeness"] < 1.0:
        reasons.append(f"analysis missing fields: {sorted(required - have)}")

    # 2. Severity consistency vs input signal + customer count
    incoming_sev = run["signal"]["severity"]
    customers = run["node_context"].get("customers_served", 0)
    resolved_sev = (analysis or {}).get("severity")
    if resolved_sev:
        order = ["low", "medium", "high", "critical"]
        if order.index(resolved_sev) >= order.index(incoming_sev):
            criteria["severity_consistency"] = 1.0
        else:
            criteria["severity_consistency"] = 0.0
            reasons.append("severity silently downgraded")
        if customers > 5000 and resolved_sev not in ("high", "critical"):
            criteria["severity_consistency"] = min(criteria["severity_consistency"], 0.5)
            reasons.append("high customer count but severity not escalated")
    else:
        criteria["severity_consistency"] = 0.0

    # 3. Ticket created
    criteria["ticket_created"] = 1.0 if coord.get("ticket") else 0.0
    if not coord.get("ticket"):
        reasons.append("no D365 ticket created")

    # 4. Dispatch decision matches policy
    sev = resolved_sev or incoming_sev
    if sev in ("high", "critical"):
        criteria["dispatch_policy"] = 1.0 if "DISPATCHED" in str(dispatch.get("result", "")) else 0.0
        if criteria["dispatch_policy"] < 1.0:
            reasons.append("high/critical incident without successful dispatch")
    else:
        criteria["dispatch_policy"] = 1.0 if not dispatch else 0.5  # over-dispatching for low/medium

    # 5. SOP referenced
    criteria["sop_referenced"] = 1.0 if (analysis or {}).get("sop_refs") else 0.0
    if not criteria["sop_referenced"]:
        reasons.append("no SOP reference attached")

    total = sum(criteria.values()) / len(criteria)
    return {
        "run_id": run["run_id"],
        "incident_id": run["incident_id"],
        "score": round(total, 3),
        "criteria": {k: round(v, 3) for k, v in criteria.items()},
        "reasons": reasons,
    }


def suggest_improvements(scored: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Aggregate failing criteria into actionable prompt/tooling suggestions."""
    counts: dict[str, int] = {}
    for s in scored:
        for k, v in s["criteria"].items():
            if v < 1.0:
                counts[k] = counts.get(k, 0) + 1

    suggestions: list[dict[str, str]] = []
    if counts.get("analysis_completeness"):
        suggestions.append(
            {
                "target": "IncidentAnalysisAgent.instructions",
                "change": "Re-emphasise the JSON schema and add an explicit 'all keys required' rule",
                "evidence": f"{counts['analysis_completeness']} run(s) returned incomplete analysis",
            }
        )
    if counts.get("severity_consistency"):
        suggestions.append(
            {
                "target": "IncidentAnalysisAgent.instructions",
                "change": "Add a hard rule: if customers_served > 5000 then severity >= 'high'",
                "evidence": f"{counts['severity_consistency']} run(s) had inconsistent severity",
            }
        )
    if counts.get("ticket_created"):
        suggestions.append(
            {
                "target": "NetOpsCoordinatorAgent.tool_loop",
                "change": "Require create_ticket as the first tool call; add validation step before notify",
                "evidence": f"{counts['ticket_created']} run(s) skipped ticket creation",
            }
        )
    if counts.get("dispatch_policy"):
        suggestions.append(
            {
                "target": "FieldDispatchAgent.instructions",
                "change": "Strengthen escalation path when no engineer available — auto-broaden region or page duty manager",
                "evidence": f"{counts['dispatch_policy']} run(s) failed to dispatch on high/critical",
            }
        )
    if counts.get("sop_referenced"):
        suggestions.append(
            {
                "target": "IncidentAnalysisAgent.tool_loop",
                "change": "Force lookup_sop() to be called before producing the JSON",
                "evidence": f"{counts['sop_referenced']} run(s) returned no SOP reference",
            }
        )
    return suggestions


def _summarise_eval_results(results: Any) -> dict[str, Any]:
    """Best-effort reduction of a Foundry ``EvalResults`` into a compact dict.

    The exact shape varies by SDK version, so we probe a few common surfaces
    (``.metrics`` / ``.summary`` / mapping) and fall back to ``str(results)``.
    """
    for attr in ("metrics", "summary", "aggregated_metrics", "scores"):
        value = getattr(results, attr, None)
        if isinstance(value, dict) and value:
            return {"metrics": {k: value[k] for k in list(value)[:20]}}
    if isinstance(results, dict) and results:
        return {"metrics": {k: results[k] for k in list(results)[:20]}}
    return {"raw": str(results)[:2000]}


def run_foundry_evals(lookback_hours: int = 24) -> dict[str, Any] | None:
    """Run Foundry cloud Evaluators over recent agent traces, when enabled.

    Gated by ``FIBREOPS_FOUNDRY_EVALS`` (+ a configured project endpoint). When
    off — the default — this is a no-op so the optimiser stays fully offline.
    Any failure degrades gracefully to ``None`` (local rubric remains primary).
    """
    settings = get_settings()
    if not settings.foundry_evals_enabled:
        return None
    if not settings.azure_ai_project_endpoint:
        logger.warning("foundry evals enabled but AZURE_AI_PROJECT_ENDPOINT not set; skipping")
        return None
    try:
        from agent_framework_foundry import FoundryEvals, evaluate_traces
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
    except Exception as exc:  # pragma: no cover - optional cloud deps
        logger.warning("foundry evals dependencies unavailable: %s", exc)
        return None
    try:
        project_client = AIProjectClient(
            endpoint=settings.azure_ai_project_endpoint,
            credential=DefaultAzureCredential(),
            allow_preview=True,
        )
        results = evaluate_traces(
            project_client=project_client,
            model=settings.azure_ai_model_deployment,
            evaluators=[
                FoundryEvals.COHERENCE,
                FoundryEvals.RELEVANCE,
                FoundryEvals.TASK_ADHERENCE,
            ],
            lookback_hours=lookback_hours,
            eval_name="FibreOps optimiser",
        )
        summary = _summarise_eval_results(results)
        record_event("fibreops.optimiser.foundry_evals", lookback_hours=lookback_hours)
        logger.info("foundry evals completed", extra={"lookback_hours": lookback_hours})
        return summary
    except Exception as exc:  # pragma: no cover - network/cloud path
        logger.warning("foundry evals run failed, using local rubric only: %s", exc)
        return None


def run_optimisation() -> dict[str, Any]:
    runs = _load_runs()
    if not runs:
        logger.info("optimiser: no runs to evaluate")
        return {"runs": 0, "scores": [], "suggestions": []}
    tracer = get_tracer()
    scored: list[dict[str, Any]] = []
    with tracer.start_as_current_span("optimiser.run") as opt_span:
        opt_span.set_attribute("runs", len(runs))
        for r in runs:
            s = score_run(r)
            scored.append(s)
            # Per-run summary event — feeds the score-trend KQL chart.
            record_event(
                "fibreops.optimiser.score",
                run_id=s["run_id"],
                incident_id=s["incident_id"],
                score=s["score"],
            )
            # Per-criterion event — feeds the failing-criteria KQL bar chart.
            for criterion, value in s["criteria"].items():
                record_event(
                    "fibreops.optimiser.criterion",
                    run_id=s["run_id"],
                    incident_id=s["incident_id"],
                    criterion=criterion,
                    score=value,
                    passed=value >= 1.0,
                )
        suggestions = suggest_improvements(scored)
        opt_span.set_attribute(
            "avg_score", round(sum(s["score"] for s in scored) / len(scored), 3)
        )
        opt_span.set_attribute("suggestions", len(suggestions))
    summary = {
        "runs": len(runs),
        "avg_score": round(sum(s["score"] for s in scored) / len(scored), 3),
        "scores": scored,
        "suggestions": suggestions,
    }
    # Optional: augment the local rubric with Foundry cloud Evaluators.
    foundry = run_foundry_evals()
    if foundry is not None:
        summary["foundry_evals"] = foundry
    with SUGGESTIONS_FILE.open("w", encoding="utf-8") as f:
        f.write(json.dumps(summary, indent=2, default=str))
    return summary
