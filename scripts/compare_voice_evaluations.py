"""Compare completed runs on identical cases without hiding failed gates."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def compare_runs(paths: list[Path]) -> dict:
    if len(paths) < 2:
        raise ValueError("At least two completed evaluation directories are required")
    reference = None
    reference_protocol = None
    runs = []
    for path in paths:
        summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
        result = json.loads((path / "sdk-results.json").read_text(encoding="utf-8"))
        if not summary.get("cloud_verified") or summary["portal_run"]["status"] != "completed":
            raise ValueError(f"Unverified evaluation: {path.name}")
        protocol = tuple(summary.get(key, "legacy-unversioned") for key in (
            "evaluation_protocol", "language_rubric_version", "transcript_protocol", "judge_model",
        ))
        if reference_protocol is not None and protocol != reference_protocol:
            raise ValueError("Runs must use identical evaluation, transcript and judge protocols")
        reference_protocol = protocol
        rows = result["rows"]
        contracts = {
            (row["inputs.case"], row["inputs.repeat"]): (
                row["inputs.query"], row["inputs.expected_language"],
                row["inputs.expected_meaning"], row.get("inputs.split", "regression"),
                row.get("inputs.evaluation_protocol", "legacy-unversioned"),
                row.get("inputs.language_rubric_version", "legacy-unversioned"),
                row.get("inputs.transcript_protocol", "legacy-unversioned"),
                row.get("inputs.case_manifest_sha256", "legacy-unversioned"),
            )
            for row in rows
        }
        if len(contracts) != len(rows) or len(rows) != summary["rows"]:
            raise ValueError("Incomplete or duplicate comparison rows")
        if reference is not None and contracts != reference:
            raise ValueError("Runs must use identical case, language, repeat and factual contracts")
        reference = contracts
        runs.append({
            "label": path.name, "rows": len(rows),
            "by_response_language": summary["by_response_language"],
            "by_split": summary.get("by_split", {}),
            "portal_run": summary["portal_run"], "human_listening_approved": False,
            "collection_recovery": summary.get("collection_recovery"),
            "evaluation_protocol": summary.get("evaluation_protocol", "legacy-unversioned"),
            "listening_review_status": summary.get("listening_review_status", "not_performed"),
        })
    return {
        "same_dataset_verified": True, "runs": runs,
        "note": "Compare minima and failures as well as averages. Text scores do not certify native pronunciation.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare_runs(args.runs)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
    for run in result["runs"]:
        print(run["label"], json.dumps(run["portal_run"]["result_counts"]))


if __name__ == "__main__":
    main()
