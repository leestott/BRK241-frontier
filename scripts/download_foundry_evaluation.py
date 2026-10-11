"""Download complete per-row results for a Microsoft Foundry evaluation run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import AzureCliCredential


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-endpoint", required=True)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    credential = AzureCliCredential()
    with AIProjectClient(
        endpoint=args.project_endpoint,
        credential=credential,
        allow_preview=True,
    ) as project:
        with project.get_openai_client() as client:
            run = client.evals.runs.retrieve(
                run_id=args.run_id,
                eval_id=args.evaluation_id,
            )
            items = list(
                client.evals.runs.output_items.list(
                    run_id=args.run_id,
                    eval_id=args.evaluation_id,
                )
            )

    result = {
        "evaluationId": args.evaluation_id,
        "runId": args.run_id,
        "name": run.name,
        "status": run.status,
        "reportUrl": run.report_url,
        "resultCounts": run.result_counts.model_dump(mode="json"),
        "items": [item.model_dump(mode="json") for item in items],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"Downloaded {len(items)} rows "
        f"({result['resultCounts']}) to {args.output}"
    )


if __name__ == "__main__":
    main()
