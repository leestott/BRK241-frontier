"""Optimiser rubric tests.

Cover every criterion the optimiser scores, plus the suggestion aggregator
and the empty-runs path. Uses synthetic run records so we don't depend on
the full agent pipeline.
"""
from __future__ import annotations

from fibreops.optimiser import score_run, suggest_improvements, run_optimisation


def _baseline_run(**overrides):
    """A perfectly-scoring synthetic run record. Tests deviate from this."""
    run = {
        "run_id": "run-001",
        "incident_id": "INC-001",
        "signal": {"severity": "high"},
        "node_context": {"customers_served": 1000},
        "steps": [
            {
                "agent": "IncidentAnalysisAgent",
                "output": {
                    "summary": "s",
                    "probable_cause": "pc",
                    "customer_impact": "ci",
                    "severity": "high",
                    "recommended_actions": ["a1"],
                    "sop_refs": ["sop_loss_of_light"],
                },
            },
            {"agent": "NetOpsCoordinatorAgent", "ticket": {"ticket_id": "TKT-1"}},
            {"agent": "FieldDispatchAgent", "result": "DISPATCHED Foo ETA 18 min"},
        ],
    }
    run.update(overrides)
    return run


def test_score_run_full_marks_for_perfect_run():
    s = score_run(_baseline_run())
    assert s["score"] == 1.0
    assert s["reasons"] == []
    assert set(s["criteria"]) == {
        "analysis_completeness",
        "severity_consistency",
        "ticket_created",
        "dispatch_policy",
        "sop_referenced",
    }


def test_score_run_penalises_incomplete_analysis():
    run = _baseline_run()
    # Drop two required fields
    del run["steps"][0]["output"]["probable_cause"]
    del run["steps"][0]["output"]["customer_impact"]
    s = score_run(run)
    assert s["criteria"]["analysis_completeness"] < 1.0
    assert any("missing fields" in r for r in s["reasons"])


def test_score_run_flags_silent_severity_downgrade():
    run = _baseline_run()
    run["signal"]["severity"] = "critical"
    run["steps"][0]["output"]["severity"] = "medium"
    s = score_run(run)
    assert s["criteria"]["severity_consistency"] == 0.0
    assert "severity silently downgraded" in s["reasons"]


def test_score_run_flags_high_customers_low_severity():
    run = _baseline_run()
    run["node_context"]["customers_served"] = 20000
    run["steps"][0]["output"]["severity"] = "medium"
    run["signal"]["severity"] = "medium"
    s = score_run(run)
    assert s["criteria"]["severity_consistency"] <= 0.5
    assert any("customer count" in r for r in s["reasons"])


def test_score_run_no_ticket_zeroes_ticket_criterion():
    run = _baseline_run()
    run["steps"][1] = {"agent": "NetOpsCoordinatorAgent"}  # no ticket key
    s = score_run(run)
    assert s["criteria"]["ticket_created"] == 0.0
    assert "no D365 ticket created" in s["reasons"]


def test_score_run_high_severity_without_dispatch_fails_policy():
    run = _baseline_run()
    run["steps"] = run["steps"][:2]  # drop dispatch step entirely
    s = score_run(run)
    assert s["criteria"]["dispatch_policy"] == 0.0
    assert "high/critical incident without successful dispatch" in s["reasons"]


def test_score_run_low_severity_over_dispatch_penalty():
    run = _baseline_run()
    run["signal"]["severity"] = "low"
    run["steps"][0]["output"]["severity"] = "low"
    # Dispatching on a low-severity run is over-dispatch (0.5).
    s = score_run(run)
    assert s["criteria"]["dispatch_policy"] == 0.5


def test_score_run_no_sop_ref_fails():
    run = _baseline_run()
    run["steps"][0]["output"]["sop_refs"] = []
    s = score_run(run)
    assert s["criteria"]["sop_referenced"] == 0.0
    assert "no SOP reference attached" in s["reasons"]


def test_suggest_improvements_aggregates_failures():
    scored = [
        {
            "criteria": {
                "analysis_completeness": 0.5,
                "severity_consistency": 1.0,
                "ticket_created": 1.0,
                "dispatch_policy": 0.0,
                "sop_referenced": 0.0,
            }
        },
        {
            "criteria": {
                "analysis_completeness": 1.0,
                "severity_consistency": 0.0,
                "ticket_created": 0.0,
                "dispatch_policy": 1.0,
                "sop_referenced": 0.0,
            }
        },
    ]
    sugg = suggest_improvements(scored)
    targets = {s["target"] for s in sugg}
    assert "IncidentAnalysisAgent.instructions" in targets
    assert "NetOpsCoordinatorAgent.tool_loop" in targets
    assert "FieldDispatchAgent.instructions" in targets
    # SOP count of 2 should be reflected in the evidence string.
    sop_sugg = next(s for s in sugg if s["target"] == "IncidentAnalysisAgent.tool_loop")
    assert "2" in sop_sugg["evidence"]


def test_run_optimisation_returns_empty_when_no_runs(chdir_state_tmp):
    summary = run_optimisation()
    assert summary == {"runs": 0, "scores": [], "suggestions": []}


def test_run_optimisation_scores_and_persists(chdir_state_tmp):
    import json

    runs_file = chdir_state_tmp / "state" / "runs.jsonl"
    runs_file.write_text(json.dumps(_baseline_run()) + "\n", encoding="utf-8")
    summary = run_optimisation()
    assert summary["runs"] == 1
    assert summary["avg_score"] == 1.0
    assert (chdir_state_tmp / "state" / "optimiser_suggestions.jsonl").exists()
