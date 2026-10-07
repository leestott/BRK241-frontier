"""Evaluate real-audio replies with the Azure AI Evaluation SDK and log to Foundry."""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
from azure.ai.evaluation import CoherenceEvaluator, FluencyEvaluator, evaluate
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.identity.aio import DefaultAzureCredential as AsyncCredential

from scripts.validate_voice_audio import CASES, Case, judge

THRESHOLDS = {
    "fluency.fluency": 4,
    "coherence.coherence": 4,
    "language_contract.language_match": 1,
    "language_contract.semantic_accuracy": 1,
    "language_contract.natural_language": 1,
    "language_contract.input_recognition": 1,
}


class VoiceLanguageContractEvaluator:
    """SDK custom evaluator for language, semantic fidelity and transcription."""

    def __init__(self, endpoint: str, model: str):
        self.endpoint = endpoint
        self.model = model

    def __call__(
        self, *, query: str, response: str, expected_language: str,
        expected_meaning: str, input_transcript: str, **kwargs,
    ) -> dict:
        async def assess() -> dict:
            async with AsyncCredential() as credential, httpx.AsyncClient(timeout=90) as client:
                token = (await credential.get_token("https://cognitiveservices.azure.com/.default")).token
                return await judge(
                    client, self.endpoint, token, self.model,
                    Case("sdk-evaluation", "", expected_language, query, expected_meaning),
                    input_transcript, response,
                )

        assessment = asyncio.run(assess())
        return {
            "language_match": int(assessment["language_ok"]),
            "semantic_accuracy": int(assessment["meaning_ok"]),
            "natural_language": int(assessment["language_natural"]),
            "input_recognition": int(assessment["input_understood"]),
            "reason": assessment["reason"],
        }


def dataset_rows(report: dict) -> list[dict]:
    cases = {case.name: case for case in CASES}
    results = report["results"]
    if not results or len(results) != report["expected_turns"]:
        raise ValueError("Refusing to evaluate an incomplete audio run")
    seen = set()
    rows = []
    for result in results:
        case = cases[result["case"]]
        key = (result["repeat"], case.name)
        if key in seen:
            raise ValueError("Duplicate audio case")
        seen.add(key)
        if result["expected_language"] != case.expected_language:
            raise ValueError("Audio report uses an outdated language policy")
        if not result["output_transcript"] or result["audio_metrics"]["rms"] <= 0:
            raise ValueError("Missing response or silent audio")
        rows.append({
            "case": case.name, "repeat": result["repeat"],
            "query": case.question, "response": result["output_transcript"],
            "expected_language": case.expected_language, "expected_meaning": case.meaning,
            "input_transcript": result["input_transcript"],
            "audio_file": result["audio"],
        })
    repeats = {row["repeat"] for row in rows}
    if seen != {(repeat, name) for repeat in repeats for name in cases}:
        raise ValueError("Incomplete per-conversation case coverage")
    return rows


def check_scores(rows: list[dict], expected_count: int) -> None:
    if len(rows) != expected_count:
        raise ValueError("Evaluation did not return every input row")
    failures = []
    for index, row in enumerate(rows):
        for metric, threshold in THRESHOLDS.items():
            value = row.get("outputs." + metric)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < threshold:
                failures.append(f"row {index}: {metric}={value!r}, required >= {threshold}")
    if failures:
        raise ValueError("Evaluation gate failed:\n" + "\n".join(failures))


def language_summary(rows: list[dict]) -> dict:
    summary = {}
    for language in ("en", "pl", "ar"):
        selected = [row for row in rows if row["inputs.expected_language"] == language]
        if not selected:
            raise ValueError(f"Missing evaluated responses for language {language}")
        metrics = {}
        for metric, threshold in THRESHOLDS.items():
            scores = [row.get("outputs." + metric) for row in selected]
            if any(not isinstance(score, (int, float)) or not math.isfinite(score) for score in scores):
                raise ValueError(f"Missing or nonfinite {language} score for {metric}")
            passed = sum(score >= threshold for score in scores)
            metrics[metric] = {
                "average": sum(scores) / len(scores), "minimum": min(scores),
                "passed": passed, "failed": len(scores) - passed, "threshold": threshold,
            }
        summary[language] = {
            "rows": len(selected), "metrics": metrics,
            "all_gates_passed": all(metric["failed"] == 0 for metric in metrics.values()),
        }
    if sum(item["rows"] for item in summary.values()) != len(rows):
        raise ValueError("Evaluation contains an unsupported response language")
    return summary


def verify_cloud_run(project_endpoint: str, studio_url: str, credential, report: dict) -> str:
    run_id = urlparse(studio_url).path.rstrip("/").rsplit("/", 1)[-1]
    token = credential.get_token("https://ai.azure.com/.default").token
    response = httpx.get(
        project_endpoint.rstrip("/") + "/evaluations/runs/" + quote(run_id, safe=""),
        params={"api-version": "2025-11-15-preview"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    response.raise_for_status()
    run = response.json()
    if (
        run.get("id") != run_id
        or run.get("status") != "Completed"
        or not run.get("outputs", {}).get("evaluationResultId")
        or run.get("tags", {}).get("agent") != report["agent"]
        or run.get("tags", {}).get("agent_version") != report["version"]
    ):
        raise RuntimeError("Foundry did not persist a completed evaluation for the expected agent version")
    return run_id


def portal_dataset(rows: list[dict]) -> tuple[dict, list[dict], list[dict]]:
    properties = {
        key: {"type": "string"}
        for key in ("case", "query", "response", "sdk_reason", "fluency_reason", "coherence_reason", "expected_language")
    }
    criteria, items = [], []
    for metric, threshold in THRESHOLDS.items():
        key = metric.replace(".", "_")
        properties[key] = {"type": "number"}
        properties[key + "_gate"] = {"type": "string"}
        criteria.append({
            "type": "string_check", "name": key,
            "input": "{{item." + key + "_gate}}", "reference": "pass", "operation": "eq",
        })
    for row in rows:
        item = {key: row["inputs." + key] for key in ("case", "query", "response", "expected_language")}
        item["sdk_reason"] = row["outputs.language_contract.reason"]
        item["fluency_reason"] = row["outputs.fluency.fluency_reason"]
        item["coherence_reason"] = row["outputs.coherence.coherence_reason"]
        for metric, threshold in THRESHOLDS.items():
            score = row["outputs." + metric]
            if not isinstance(score, (int, float)) or not math.isfinite(score):
                raise ValueError("Cannot publish missing or nonfinite SDK scores")
            key = metric.replace(".", "_")
            item[key] = score
            item[key + "_gate"] = "pass" if score >= threshold else "fail"
        items.append({"item": item})
    return {"type": "object", "properties": properties, "required": list(properties)}, criteria, items


def publish_portal_run(endpoint: str, credential, result: dict, report: dict, output_dir: Path) -> dict:
    schema, criteria, items = portal_dataset(result["rows"])
    metadata = {
        "agent": report["agent"], "agent_version": report["version"],
        "score_source": "azure-ai-evaluation SDK; deterministic acceptance gates",
        "human_listening_approved": "false",
        "policy": "english-default-explicit-pl-ar-per-turn",
        "sdk_run_id": urlparse(result["studio_url"]).path.rstrip("/").rsplit("/", 1)[-1],
    }
    with AIProjectClient(endpoint=endpoint, credential=credential) as project:
        with project.get_openai_client() as client:
            evaluation = client.evals.create(
                name=f"{report['agent']}-v{report['version']}-SDK-acceptance",
                data_source_config={"type": "custom", "item_schema": schema},
                testing_criteria=criteria, metadata=metadata,
            )
            run = client.evals.runs.create(
                evaluation.id, name=f"Real audio: {len(items)} turns",
                data_source={"type": "jsonl", "source": {"type": "file_content", "content": items}},
                metadata=metadata,
            )
            identity = {"eval_id": evaluation.id, "run_id": run.id, "report_url": run.report_url}
            (output_dir / "portal-run.json").write_text(json.dumps(identity, indent=2), encoding="utf-8")
            deadline = time.monotonic() + 600
            while run.status in {"queued", "in_progress"}:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Foundry evaluation still running: {run.id}")
                time.sleep(5)
                run = client.evals.runs.retrieve(run.id, eval_id=evaluation.id)
            if run.status != "completed":
                raise RuntimeError(f"Foundry evaluation {run.id} ended with {run.status}: {run.error}")
            outputs = list(client.evals.runs.output_items.list(run.id, eval_id=evaluation.id))
            if len(outputs) != len(items) or run.result_counts.errored:
                raise RuntimeError("Foundry evaluation has missing or errored rows")
            expected_passes = sum(
                all(entry["item"][metric.replace(".", "_") + "_gate"] == "pass" for metric in THRESHOLDS)
                for entry in items
            )
            if run.result_counts.passed != expected_passes:
                raise RuntimeError("Foundry pass counts do not match the original SDK acceptance gates")
            (output_dir / "portal-results.json").write_text(
                json.dumps([item.model_dump(mode="json") for item in outputs], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            identity.update({
                "status": run.status, "report_url": run.report_url,
                "result_counts": run.result_counts.model_dump(),
            })
            (output_dir / "portal-run.json").write_text(json.dumps(identity, indent=2), encoding="utf-8")
            return identity


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio-report", type=Path, required=True)
    parser.add_argument("--project-endpoint", required=True)
    parser.add_argument("--judge-endpoint", required=True)
    parser.add_argument("--judge-model", default="gpt-5.4-mini")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sdk-results", type=Path, help="Publish existing SDK results without rescoring")
    args = parser.parse_args()
    report = json.loads(args.audio_report.read_text(encoding="utf-8"))
    rows = dataset_rows(report)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    data_path = args.output_dir / "dataset.jsonl"
    data_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8",
    )
    config = {
        "azure_endpoint": args.judge_endpoint,
        "azure_deployment": args.judge_model,
        "api_version": "2025-04-01-preview",
    }
    with DefaultAzureCredential() as credential:
        result = json.loads(args.sdk_results.read_text(encoding="utf-8")) if args.sdk_results else evaluate(
            data=str(data_path),
            evaluation_name=f"{report['agent']}-v{report['version']}-spoken-language",
            evaluators={
                "fluency": FluencyEvaluator(config, credential=credential, threshold=4, is_reasoning_model=True),
                "coherence": CoherenceEvaluator(config, credential=credential, threshold=4, is_reasoning_model=True),
                "language_contract": VoiceLanguageContractEvaluator(args.judge_endpoint, args.judge_model),
            },
            azure_ai_project=args.project_endpoint,
            output_path=str(args.output_dir / "sdk-results.json"),
            fail_on_evaluator_errors=True,
            tags={
                "agent": report["agent"], "agent_version": report["version"],
                "input": "real-audio", "policy": "english-default-explicit-pl-ar-per-turn",
                "human_listening_approved": "false",
            },
        )
        studio_url = result.get("studio_url")
        if not studio_url:
            raise RuntimeError("SDK did not return a Foundry evaluation URL; cloud persistence is unverified")
        run_id = verify_cloud_run(args.project_endpoint, studio_url, credential, report)
        if len(result["rows"]) != len(rows):
            raise ValueError("SDK results do not cover the entire audio report")
        for source, scored in zip(rows, result["rows"]):
            if any(scored.get("inputs." + key) != value for key, value in source.items()):
                raise ValueError("SDK results do not match the supplied audio report")
        portal_run = publish_portal_run(args.project_endpoint, credential, result, report, args.output_dir)
    summary = {
        "studio_url": studio_url, "run_id": run_id, "cloud_verified": True,
        "metrics": result["metrics"],
        "by_response_language": language_summary(result["rows"]),
        "portal_run": portal_run,
        "rows": len(result["rows"]), "human_listening_approved": False,
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    check_scores(result["rows"], len(rows))
    print("PASS: all per-row SDK evaluation thresholds met. Native-speaker audio approval still required.")


if __name__ == "__main__":
    main()
