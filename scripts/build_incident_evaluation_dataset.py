"""Build a Foundry evaluation dataset from recorded FibreOps incident runs."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


_RUNTIME_TEXT_PATTERNS = (
    (re.compile(r"\b20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\b"), "<timestamp>"),
    (re.compile(r"\brun-[A-Za-z0-9-]+\b"), "<run-id>"),
    (re.compile(r"\bsig-[A-Za-z0-9-]+\b"), "<signal-id>"),
    (re.compile(r"\bBK-[A-Za-z0-9-]+\b"), "<booking-id>"),
)


def _load_runs(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return []

    if text.startswith("[") or text.startswith("{"):
        payload = json.loads(text)
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict) and isinstance(payload.get("runs"), list):
            return payload["runs"]
        if isinstance(payload, dict):
            return [payload]

    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _stable_ticket(ticket: Any) -> Any:
    if not isinstance(ticket, dict):
        return ticket
    return {
        key: value
        for key, value in ticket.items()
        if key not in {"created_at", "events"}
    }


def _without_runtime_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_runtime_fields(item)
            for key, item in value.items()
            if key not in {"booking_id", "created_at", "run_id", "signal_id", "timestamp"}
        }
    if isinstance(value, list):
        return [_without_runtime_fields(item) for item in value]
    if isinstance(value, str):
        for pattern, replacement in _RUNTIME_TEXT_PATTERNS:
            value = pattern.sub(replacement, value)
    return value


def _context(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "incident_id": run.get("incident_id"),
        "signal": _without_runtime_fields(run.get("signal")),
        "node_context": run.get("node_context"),
    }


def build_rows(runs: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for run in runs:
        context = _context(run)
        incident_id = str(run.get("incident_id", "unknown"))
        signal = run.get("signal") or {}
        node = run.get("node_context") or {}
        steps = run.get("steps") or []

        for step in steps:
            agent = step.get("agent")
            if agent == "IncidentAnalysisAgent":
                response = _without_runtime_fields(step.get("output"))
                ground_truth = {
                    "requirements": [
                        f"Identify incident {incident_id} at node {signal.get('node_id')}.",
                        (
                            f"Preserve the recorded signal severity {signal.get('severity')} "
                            f"and measurement {signal.get('measured_value')} {signal.get('unit')}."
                        ),
                        (
                            f"Use the recorded site {node.get('site')} and customer exposure "
                            f"{node.get('customers_served')}."
                        ),
                        "Qualify probable causes when evidence is incomplete.",
                        "Provide clear, actionable operational recommendations.",
                    ]
                }
                rows.append(
                    {
                        "query": (
                            f"Analyze recorded incident {incident_id} from its signal and node "
                            "facts. Explain the likely cause, customer impact, severity, and "
                            "recommended actions without inventing evidence."
                        ),
                        "response": json.dumps(response, ensure_ascii=False, sort_keys=True),
                        "context": json.dumps(context, ensure_ascii=False, sort_keys=True),
                        "ground_truth": json.dumps(
                            ground_truth, ensure_ascii=False, sort_keys=True
                        ),
                    }
                )
            elif agent == "NetOpsCoordinatorAgent":
                coordinator_context = {
                    **context,
                    "incident_analysis": next(
                        (
                            item.get("output")
                            for item in steps
                            if item.get("agent") == "IncidentAnalysisAgent"
                        ),
                        None,
                    ),
                }
                response = {
                    "decision": step.get("decision"),
                    "ticket": _stable_ticket(step.get("ticket")),
                }
                response = _without_runtime_fields(response)
                expected_action = (
                    "dispatch or escalate"
                    if signal.get("severity") in {"critical", "high"}
                    else "monitor with a proportionate ticket"
                )
                ground_truth = {
                    "requirements": [
                        f"Keep incident ID {incident_id} and node ID {signal.get('node_id')} exact.",
                        f"Do not downgrade the recorded {signal.get('severity')} signal severity.",
                        f"The operational decision should {expected_action}.",
                        "Return readable operational text, not leaked tool-call syntax or malformed JSON.",
                        "Keep the ticket facts consistent with the recorded signal and analysis.",
                    ]
                }
                rows.append(
                    {
                        "query": (
                            f"Coordinate recorded incident {incident_id}: make the escalation or "
                            "monitoring decision and produce a factually consistent incident ticket."
                        ),
                        "response": json.dumps(response, ensure_ascii=False, sort_keys=True),
                        "context": json.dumps(
                            coordinator_context, ensure_ascii=False, sort_keys=True
                        ),
                        "ground_truth": json.dumps(
                            ground_truth, ensure_ascii=False, sort_keys=True
                        ),
                    }
                )
            elif agent == "FieldDispatchAgent":
                response = {
                    "result": step.get("result"),
                    "metadata": _without_runtime_fields(step.get("metadata")),
                }
                ground_truth = {
                    "requirements": [
                        f"Associate the dispatch with incident {incident_id}.",
                        "Report dispatch status, engineer identity, and ETA consistently.",
                        "Do not invent dispatch details beyond the recorded metadata.",
                    ]
                }
                rows.append(
                    {
                        "query": (
                            f"Report the recorded field-dispatch outcome for incident {incident_id} "
                            "with the assigned engineer and ETA."
                        ),
                        "response": json.dumps(response, ensure_ascii=False, sort_keys=True),
                        "context": json.dumps(context, ensure_ascii=False, sort_keys=True),
                        "ground_truth": json.dumps(
                            ground_truth, ensure_ascii=False, sort_keys=True
                        ),
                    }
                )

    return rows


def build_technical_rows(runs: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for run in runs:
        signal = run.get("signal") or {}
        node = run.get("node_context") or {}
        steps = run.get("steps") or []
        incident_id = str(run.get("incident_id", "unknown"))
        analysis = next(
            (
                step.get("output")
                for step in steps
                if step.get("agent") == "IncidentAnalysisAgent"
            ),
            None,
        )
        coordinator = next(
            (
                step
                for step in steps
                if step.get("agent") == "NetOpsCoordinatorAgent"
            ),
            None,
        )
        if not isinstance(analysis, dict) or not isinstance(coordinator, dict):
            continue
        dispatch = next(
            (
                step
                for step in steps
                if step.get("agent") == "FieldDispatchAgent"
            ),
            None,
        )
        dispatch_metadata = (
            (dispatch.get("metadata") or {}).get("dispatch") or {}
            if isinstance(dispatch, dict)
            else {}
        )
        ticket = _stable_ticket(coordinator.get("ticket")) or {}
        response = _without_runtime_fields(
            {
                "analysis": {
                    key: analysis.get(key)
                    for key in (
                        "severity",
                        "summary",
                        "customer_impact",
                        "probable_cause",
                    )
                },
                "decision": coordinator.get("decision"),
                "ticket": {
                    key: ticket.get(key)
                    for key in (
                        "ticket_id",
                        "incident_id",
                        "node_id",
                        "severity",
                        "title",
                        "status",
                    )
                },
                "dispatch": (
                    {
                        "result": dispatch.get("result"),
                        "engineer_id": dispatch_metadata.get("engineer_id"),
                        "engineer_name": dispatch_metadata.get("engineer_name"),
                        "eta_minutes": dispatch_metadata.get("eta_minutes"),
                    }
                    if isinstance(dispatch, dict)
                    else None
                ),
            }
        )
        expected_action = (
            "dispatch or urgently escalate"
            if signal.get("severity") in {"critical", "high"}
            else "monitor and investigate proportionately"
        )
        raw = signal.get("raw") or {}
        requirements = [
            f"Keep incident {incident_id} and node {signal.get('node_id')} exact.",
            (
                f"Preserve {signal.get('severity')} severity and the exact measurement "
                f"{signal.get('measured_value')} {signal.get('unit')}."
            ),
            (
                f"Use site {node.get('site')} and customer exposure "
                f"{node.get('customers_served')} without approximation."
            ),
            f"The operational decision must {expected_action}.",
            "Keep the ticket severity, node, and incident identifiers consistent.",
            "Use readable operational text without code fences, tool-call syntax, or garbled text.",
        ]
        if raw.get("consecutive_polls_dark") is not None:
            requirements.append(
                f"Preserve {raw['consecutive_polls_dark']} consecutive dark polls."
            )
        if raw.get("last_good_dbm") is not None:
            requirements.append(
                f"Preserve the last good optical reading {raw['last_good_dbm']} dBm."
            )
        if raw.get("olt_port"):
            requirements.append(f"Preserve OLT port {raw['olt_port']}.")
        if signal.get("severity") in {"critical", "high"}:
            requirements.append(
                "Include a recorded field dispatch or clearly identify a missing dispatch as a defect."
            )
        rows.append(
            {
                "query": (
                    f"Perform an end-to-end technical consistency review for recorded "
                    f"incident {incident_id}, including analysis, decision, ticket, and dispatch."
                ),
                "response": json.dumps(response, ensure_ascii=False, sort_keys=True),
                "context": json.dumps(
                    _context(run), ensure_ascii=False, sort_keys=True
                ),
                "ground_truth": json.dumps(
                    {"requirements": requirements},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="Recorded runs as JSON or JSONL")
    parser.add_argument("output", type=Path, help="Output Foundry JSONL dataset")
    parser.add_argument(
        "--technical-only",
        action="store_true",
        help="Generate one end-to-end technical consistency row per incident",
    )
    args = parser.parse_args()

    runs = _load_runs(args.input)
    rows = build_technical_rows(runs) if args.technical_only else build_rows(runs)
    if not rows:
        raise SystemExit("No incident evaluation rows were generated.")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"Wrote {len(rows)} evaluation rows to {args.output}")


if __name__ == "__main__":
    main()
