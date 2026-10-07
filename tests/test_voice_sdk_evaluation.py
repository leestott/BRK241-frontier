"""Fail-closed gates for optional Foundry SDK evaluation tooling."""
import pytest
from types import SimpleNamespace

pytest.importorskip("azure.ai.evaluation", reason="Install requirements-evaluation.txt for SDK checks")

from scripts.evaluate_voice_responses import THRESHOLDS, check_scores, dataset_rows, language_summary, portal_dataset, verify_cloud_run
from scripts.validate_voice_audio import CASES


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
