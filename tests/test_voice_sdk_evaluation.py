"""Fail-closed gates for optional Foundry SDK evaluation tooling."""
import pytest
import asyncio
import json
import pickle
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

pytest.importorskip("azure.ai.evaluation", reason="Install requirements-evaluation.txt for SDK checks")

from scripts.evaluate_voice_responses import THRESHOLDS, check_scores, dataset_rows, language_summary, portal_dataset, verify_cloud_run
from scripts.validate_voice_audio import CASES
from scripts.evaluate_voice_responses import EvaluationCredential, VoiceLanguageContractEvaluator
from azure.core.credentials import AccessToken
from azure.ai.evaluation import evaluate


class AsyncProbeEvaluator:
    async def __call__(self, *, query: str, **kwargs):
        await asyncio.sleep(0)
        return {"score": len(query)}


def test_sdk_batch_executes_async_evaluator_without_nested_event_loops(tmp_path, monkeypatch):
    monkeypatch.setenv("PF_WORKER_COUNT", "1")
    data = tmp_path / "async-probe.jsonl"
    queries = ["one", "second", "third"]
    data.write_text(
        "".join(json.dumps({"query": query}) + "\n" for query in queries),
        encoding="utf-8",
    )
    result = evaluate(
        data=str(data), evaluators={"probe": AsyncProbeEvaluator()},
        fail_on_evaluator_errors=True,
    )
    assert len(result["rows"]) == len(queries)
    for row, query in zip(result["rows"], queries):
        assert row["outputs.probe.score"] == len(query)


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


def test_evaluation_credential_caches_concurrent_requests_and_refreshes(monkeypatch):
    calls = []
    clock = [1000]

    def get_token(*scopes, **kwargs):
        calls.append((scopes, kwargs))
        return AccessToken("test-token", clock[0] + 3600)

    monkeypatch.setattr("scripts.evaluate_voice_responses.time.time", lambda: clock[0])
    monkeypatch.setattr(
        "scripts.evaluate_voice_responses.DefaultAzureCredential",
        lambda **kwargs: SimpleNamespace(get_token=get_token, close=lambda: None),
    )
    with EvaluationCredential() as credential:
        with ThreadPoolExecutor(max_workers=4) as pool:
            tokens = list(pool.map(lambda _: credential.get_token("scope"), range(20)))
        assert len(calls) == 1
        assert all(token.token == "test-token" for token in tokens)
        credential.get_token("other-scope")
        assert len(calls) == 2
        clock[0] = 4400
        credential.get_token("scope")
        assert len(calls) == 3
        credential.get_token("scope", claims="new-claims")
        assert calls[-1][1] == {"claims": "new-claims"}
        serialized = pickle.dumps(credential)
        assert b"test-token" not in serialized
        restored = pickle.loads(serialized)
        with restored:
            restored.get_token("scope")
        assert len(calls) == 5


@pytest.mark.asyncio
async def test_language_evaluator_runs_inside_sdk_event_loop(monkeypatch):
    monkeypatch.setattr(
        "scripts.evaluate_voice_responses.DefaultAzureCredential",
        lambda **kwargs: SimpleNamespace(
            get_token=lambda *args: AccessToken("test-token", 9999999999),
            close=lambda: None,
        ),
    )

    async def fake_judge(client, endpoint, token, model, case, input_text, output_text):
        assert token == "test-token"
        assert input_text == "Question"
        assert output_text == "Response"
        return {
            "language_ok": True, "meaning_ok": False,
            "language_natural": True, "input_understood": True, "reason": "Fact changed",
        }

    monkeypatch.setattr("scripts.evaluate_voice_responses.judge", fake_judge)
    evaluator = VoiceLanguageContractEvaluator("https://example.test", "judge")
    try:
        result = await evaluator(
            query="Question", response="Response", expected_language="en",
            expected_meaning="Exact fact", input_transcript="Question",
        )
    finally:
        evaluator.credential.close()
    assert result == {
        "language_match": 1, "semantic_accuracy": 0,
        "natural_language": 1, "input_recognition": 1, "reason": "Fact changed",
    }
