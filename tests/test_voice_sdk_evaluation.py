"""Fail-closed gates for optional Foundry SDK evaluation tooling."""
import pytest
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from types import SimpleNamespace

pytest.importorskip("azure.ai.evaluation", reason="Install requirements-evaluation.txt for SDK checks")

from scripts.evaluate_voice_responses import (
    INCIDENT_THRESHOLDS, THRESHOLDS, IncidentBriefingEvaluator, JudgeTokenProvider,
    check_scores, dataset_rows, language_summary, portal_dataset, publish_portal_run,
    verify_cloud_run, verify_portal_rows,
)
from scripts.validate_voice_audio import CASES, TRANSCRIPT_PROTOCOL, case_manifest
from scripts.voice_incident_cases import incident_cases


def report():
    return {
        "expected_turns": len(CASES),
        "results": [
            {
                "case": case.name, "repeat": 1, "expected_language": case.expected_language,
                "input_transcript": case.question, "output_transcript": "Test response",
                "audio_metrics": {"rms": 100}, "audio": f"{case.name}.wav",
            }
            for case in CASES
        ],
    }


def test_sdk_dataset_requires_complete_current_policy_audio():
    value = report()
    assert len(dataset_rows(value)) == len(CASES)
    value["results"][1]["expected_language"] = "pl"
    with pytest.raises(ValueError, match="outdated language policy"):
        dataset_rows(value)


def test_sdk_dataset_rejects_partial_run():
    value = report()
    value["results"].pop()
    with pytest.raises(ValueError, match="incomplete audio run"):
        dataset_rows(value)


def test_dataset_verifies_segments_and_frozen_manifest():
    value = report()
    value["transcript_protocol"] = TRANSCRIPT_PROTOCOL
    value["case_manifest_sha256"] = case_manifest(CASES)[1]
    for row in value["results"]:
        row["output_transcript_segments"] = ["Preamble.", "Final answer."]
        row["output_transcript"] = "Preamble.\nFinal answer."
    assert dataset_rows(value)[0]["transcript_protocol"] == TRANSCRIPT_PROTOCOL
    value["results"][0]["output_transcript"] = "Final answer."
    with pytest.raises(ValueError, match="recorded response segments"):
        dataset_rows(value)
    value["case_manifest_sha256"] = "changed"
    with pytest.raises(ValueError, match="frozen case manifest"):
        dataset_rows(value)


def test_v2_incident_dataset_requires_new_collection_protocol():
    with pytest.raises(ValueError, match="segmented audio"):
        dataset_rows({**report(), "suite": "incidents-v2"})


def test_incident_dataset_records_tools_and_rejects_forged_gate():
    cases = incident_cases()
    value = {
        "suite": "incidents", "expected_turns": len(cases),
        "results": [
            {
                "case": case.name, "repeat": 1, "expected_language": case.expected_language,
                "input_transcript": case.question, "output_transcript": "Test incident response",
                "audio_metrics": {"rms": 100}, "audio": f"{case.name}.wav",
                "split": case.split,
                "tool_calls": [{"name": "list_recent_incidents", "arguments": {}, "allowed": True}],
                "tool_contract_passed": True,
            }
            for case in cases
        ],
    }
    rows = dataset_rows(value)
    assert len(rows) == 36
    assert rows[0]["tool_contract_passed"] == 1
    assert '"potentially_affected_customers": 800' in rows[0]["expected_meaning"]
    value["results"][0]["tool_calls"] = []
    with pytest.raises(ValueError, match="does not match"):
        dataset_rows(value)
    value["results"][0]["tool_contract_passed"] = False
    assert dataset_rows(value)[0]["tool_contract_passed"] == 0


@pytest.mark.parametrize("bad_score", [None, float("nan"), 0, "1"])
def test_sdk_gate_rejects_missing_failed_or_nonnumeric_metrics(bad_score):
    row = {
        "outputs.fluency.fluency": 5,
        "outputs.coherence.coherence": 5,
        "outputs.language_contract.language_match": bad_score,
        "outputs.language_contract.semantic_accuracy": 1,
        "outputs.language_contract.natural_language": 1,
        "outputs.language_contract.input_recognition": 1,
    }
    with pytest.raises(ValueError, match="Evaluation gate failed"):
        check_scores([row], 1)


def test_sdk_gate_rejects_missing_rows():
    with pytest.raises(ValueError, match="every input row"):
        check_scores([], 1)


def test_portal_retains_sdk_scores_and_failed_gates():
    row = {
        **{"inputs." + key: "example" for key in ("case", "query", "response", "expected_language")},
        **{"outputs." + key: threshold for key, threshold in THRESHOLDS.items()},
        "outputs.language_contract.reason": "Recorded SDK reason",
        "outputs.fluency.fluency_reason": "Recorded fluency reason",
        "outputs.coherence.coherence_reason": "Recorded coherence reason",
    }
    row["outputs.fluency.fluency"] = 3
    schema, criteria, items = portal_dataset([row])
    assert len(criteria) == 6
    assert items[0]["item"]["fluency_fluency"] == 3
    assert items[0]["item"]["fluency_fluency_gate"] == "fail"
    assert items[0]["item"]["language_contract_language_match_gate"] == "pass"
    assert items[0]["item"]["sdk_reason"] == "Recorded SDK reason"
    assert items[0]["item"]["fluency_reason"] == "Recorded fluency reason"
    assert items[0]["item"]["coherence_reason"] == "Recorded coherence reason"
    assert set(schema["required"]) == set(items[0]["item"])
    row["outputs.fluency.fluency"] = float("nan")
    with pytest.raises(ValueError, match="nonfinite"):
        portal_dataset([row])


def test_language_summary_keeps_each_language_and_failure_visible():
    rows = [
        {
            "inputs.expected_language": language,
            **{"outputs." + metric: threshold for metric, threshold in THRESHOLDS.items()},
        }
        for language in ("en", "en", "pl", "ar")
    ]
    rows[0]["outputs.fluency.fluency"] = 5
    rows[2]["outputs.fluency.fluency"] = 3
    rows[3]["outputs.language_contract.semantic_accuracy"] = 0
    summary = language_summary(rows)
    assert summary["en"]["rows"] == 2
    assert summary["en"]["metrics"]["fluency.fluency"]["average"] == 4.5
    assert summary["en"]["all_gates_passed"]
    assert summary["pl"]["metrics"]["fluency.fluency"] == {
        "average": 3, "minimum": 3, "passed": 0, "failed": 1, "threshold": 4,
    }
    assert not summary["pl"]["all_gates_passed"]
    assert not summary["ar"]["all_gates_passed"]
    rows[0]["outputs.fluency.fluency"] = float("nan")
    with pytest.raises(ValueError, match="nonfinite"):
        language_summary(rows)


def test_language_summary_rejects_missing_language_coverage():
    with pytest.raises(ValueError, match="Missing evaluated responses"):
        language_summary([])


def test_incident_portal_keeps_nine_gates_and_tool_failure_visible():
    row = {
        **{"inputs." + key: "example" for key in ("case", "query", "response", "expected_language")},
        **{"outputs." + key: threshold for key, threshold in INCIDENT_THRESHOLDS.items()},
        "inputs.split": "held-out", "inputs.tool_calls": "[]",
        "outputs.language_contract.reason": "Language reason",
        "outputs.fluency.fluency_reason": "Fluency reason",
        "outputs.coherence.coherence_reason": "Coherence reason",
        "outputs.incident.reason": "Operational reason",
    }
    row["outputs.incident.tool_use"] = 0
    schema, criteria, items = portal_dataset([row], INCIDENT_THRESHOLDS)
    assert len(criteria) == 9
    assert items[0]["item"]["incident_tool_use_gate"] == "fail"
    assert items[0]["item"]["split"] == "held-out"
    assert items[0]["item"]["incident_reason"] == "Operational reason"
    assert set(schema["required"]) == set(items[0]["item"])
    with pytest.raises(ValueError, match="incident.tool_use"):
        check_scores([row], 1, INCIDENT_THRESHOLDS)


@pytest.mark.parametrize("score", [None, "1", True, 2])
@pytest.mark.asyncio
async def test_incident_evaluator_rejects_invalid_recorded_tool_score(score):
    evaluator = IncidentBriefingEvaluator("https://example.test", "judge", lambda: "test-token")
    with pytest.raises(ValueError, match="tool-contract"):
        await evaluator(query="query", response="response", expected_meaning="facts", tool_contract_passed=score)


def test_two_custom_evaluators_finish_with_two_sdk_workers(tmp_path):
    data = tmp_path / "data.jsonl"
    row = {
        "query": "Synthetic question", "response": "Synthetic reply",
        "expected_language": "en", "expected_meaning": "Synthetic facts",
        "input_transcript": "Synthetic question", "tool_contract_passed": 1,
    }
    data.write_text("\n".join(json.dumps(row) for _ in range(4)), encoding="utf-8")
    script = """
import json
import sys
from types import SimpleNamespace
from azure.ai.evaluation import evaluate
import scripts.evaluate_voice_responses as voice

class Context:
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass
    async def post(self, *args, **kwargs):
        scores = dict(language_ok=True, meaning_ok=True, language_natural=True,
                      input_understood=True, reason="Synthetic judge",
                      fact_completeness=1, operational_clarity=4)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
            "choices": [{"message": {"content": json.dumps(scores)}}]})

voice.httpx.AsyncClient = lambda **kwargs: Context()
result = evaluate(data=sys.argv[1], evaluators={
    "language_contract": voice.VoiceLanguageContractEvaluator("https://example.test", "test", lambda: "test-token"),
    "incident": voice.IncidentBriefingEvaluator("https://example.test", "test", lambda: "test-token"),
}, fail_on_evaluator_errors=True)
assert len(result["rows"]) == 4
for row in result["rows"]:
    assert row["outputs.incident.tool_use"] == 1
    assert row["outputs.language_contract.language_match"] == 1
print("ASYNC_EVALUATORS_COMPLETE")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(data)],
        env={**os.environ, "PF_WORKER_COUNT": "2"},
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "ASYNC_EVALUATORS_COMPLETE" in completed.stdout


def test_judge_token_cache_serializes_concurrent_acquisition_and_refreshes():
    from azure.core.credentials import AccessToken

    tokens = [
        AccessToken("near-expiry", int(time.time()) + 60),
        AccessToken("fresh", int(time.time()) + 3600),
    ]
    calls = []

    def get_token(*scopes, **kwargs):
        calls.append(scopes)
        return tokens[len(calls) - 1]

    provider = JudgeTokenProvider(SimpleNamespace(get_token=get_token))
    assert provider() == "near-expiry"
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: provider(), range(12))) == ["fresh"] * 12
    assert calls == [("https://cognitiveservices.azure.com/.default",)] * 2


def test_judge_token_failure_is_not_hidden_or_cached():
    from azure.core.credentials import AccessToken
    from azure.core.exceptions import ClientAuthenticationError

    calls = []

    def get_token(*scopes, **kwargs):
        calls.append(scopes)
        if len(calls) == 1:
            raise ClientAuthenticationError("Identity service unavailable")
        return AccessToken("fresh", int(time.time()) + 3600)

    provider = JudgeTokenProvider(SimpleNamespace(get_token=get_token))
    with pytest.raises(ClientAuthenticationError, match="Identity service unavailable"):
        provider()
    assert provider() == "fresh"
    assert len(calls) == 2


@pytest.mark.parametrize("wrong_metadata", [False, True])
def test_resume_uses_existing_portal_run_without_creating_evaluations(tmp_path, monkeypatch, wrong_metadata):
    from scripts.evaluate_voice_responses import EVALUATION_PROTOCOL, LANGUAGE_RUBRIC_VERSION

    row = {
        **{"inputs." + key: "example" for key in ("case", "query", "response", "expected_language")},
        **{"outputs." + key: threshold for key, threshold in THRESHOLDS.items()},
        "outputs.language_contract.reason": "Language reason",
        "outputs.fluency.fluency_reason": "Fluency reason",
        "outputs.coherence.coherence_reason": "Coherence reason",
    }
    _, _, items = portal_dataset([row])
    metadata = {
        "agent": "candidate", "agent_version": "3",
        "score_source": "azure-ai-evaluation SDK; deterministic acceptance gates",
        "human_listening_approved": "false",
        "policy": "english-default-explicit-pl-ar-per-turn",
        "suite": "language", "collection_recovered": "false", "sdk_run_id": "sdk-id",
        "evaluation_protocol": EVALUATION_PROTOCOL, "language_rubric_version": LANGUAGE_RUBRIC_VERSION,
        "transcript_protocol": "legacy-concatenated-v1",
    }
    counts = {"passed": 1, "failed": 0, "errored": 0, "total": 1}
    payload = {
        "datasource_item_id": 0, "datasource_item": items[0]["item"],
        "results": [{"name": name.replace(".", "_"), "passed": True} for name in THRESHOLDS],
    }
    run = SimpleNamespace(
        id="run-id", metadata={**metadata, **({"agent_version": "wrong"} if wrong_metadata else {})},
        report_url="https://example.test/run", status="completed",
        result_counts=SimpleNamespace(**counts, model_dump=lambda: counts),
    )
    no_create = lambda *args, **kwargs: pytest.fail("Resume must not create another run")
    client = SimpleNamespace(evals=SimpleNamespace(
        retrieve=lambda identity: SimpleNamespace(id="eval-id", metadata=metadata),
        create=no_create,
        runs=SimpleNamespace(
            retrieve=lambda identity, **kwargs: run, create=no_create,
            output_items=SimpleNamespace(list=lambda *args, **kwargs: [
                SimpleNamespace(model_dump=lambda **kwargs: payload),
            ]),
        ),
    ))
    monkeypatch.setattr(
        "scripts.evaluate_voice_responses.AIProjectClient",
        lambda **kwargs: nullcontext(SimpleNamespace(get_openai_client=lambda: nullcontext(client))),
    )
    (tmp_path / "portal-run.json").write_text(json.dumps({"eval_id": "eval-id", "run_id": "run-id"}))
    arguments = (
        "https://example.test", None, {"rows": [row], "studio_url": "https://example.test/sdk-id"},
        {"agent": "candidate", "version": "3"}, tmp_path,
    )
    if wrong_metadata:
        with pytest.raises(ValueError, match="does not match"):
            publish_portal_run(*arguments, resume=True)
    else:
        assert publish_portal_run(*arguments, resume=True)["status"] == "completed"
        assert json.loads((tmp_path / "portal-results.json").read_text()) == [payload]


@pytest.mark.parametrize("corruption", ["score", "gate", "duplicate", "missing"])
def test_portal_row_verification_rejects_corrupted_results(corruption):
    item = {"fluency_fluency": 3, "fluency_fluency_gate": "fail"}
    outputs = [{
        "datasource_item_id": 0, "datasource_item": dict(item),
        "results": [{"name": "fluency_fluency", "passed": False}],
    }]
    if corruption == "score":
        outputs[0]["datasource_item"]["fluency_fluency"] = 4
    elif corruption == "gate":
        outputs[0]["results"][0]["passed"] = True
    elif corruption == "duplicate":
        outputs *= 2
    else:
        outputs.clear()
    with pytest.raises(ValueError, match="Foundry"):
        verify_portal_rows(outputs, [{"item": item}], {"fluency.fluency": 4})


@pytest.mark.parametrize("status,version", [("Running", "6"), ("Completed", "5")])
def test_cloud_verification_rejects_incomplete_or_wrong_version(monkeypatch, status, version):
    monkeypatch.setattr(
        "scripts.evaluate_voice_responses.httpx.get",
        lambda *args, **kwargs: SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "id": "run-id", "status": status,
                "outputs": {"evaluationResultId": "result-id"},
                "tags": {"agent": "candidate", "agent_version": version},
            },
        ),
    )
    credential = SimpleNamespace(get_token=lambda scope: SimpleNamespace(token="test-token"))
    with pytest.raises(RuntimeError, match="expected agent version"):
        verify_cloud_run(
            "https://example.test/api/projects/test", "https://ai.azure.com/evaluation/run-id",
            credential, {"agent": "candidate", "version": "6"},
        )
