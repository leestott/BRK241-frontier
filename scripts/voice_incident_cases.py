"""Synthetic, side-effect-free incident fixtures; never load production state."""
from __future__ import annotations

import json
from dataclasses import replace
from scripts.validate_voice_audio import Case


def incident_cases() -> tuple[Case, ...]:
    base = {
        "incident_id": "INC-DEMO-001", "run_id": "run-demo-001",
        "node_id": "FN-MAN-007", "region": "Manchester", "site": "Trafford",
        "severity": "critical", "signal_type": "loss_of_light",
        "potentially_affected_customers": 800, "customer_impact_confirmed": False,
        "customer_count_is_approximate": False, "suspected_cause": None,
        "cause_confirmed": False, "dispatch_status": "not_confirmed",
        "engineer": None, "technician_arrival_eta_minutes": None,
        "service_restoration_eta_minutes": None, "restoration_confirmed": False,
    }
    scenarios = (
        ("loss-of-light", {}, "Briefly give the critical incident, impact and confirmed cause.",
         "Critical loss of optical signal at FN-MAN-007 in Manchester; 800 customers MAY be affected; cause unconfirmed.", "development"),
        ("unreachable", {"signal_type": "node_unreachable", "severity": "high", "potentially_affected_customers": 1200},
         "Describe the signal and possible customer impact.",
         "High severity; node FN-MAN-007 is unreachable; 1200 customers MAY be affected. Do not assert a fibre cut.", "development"),
        ("attenuation", {"signal_type": "high_attenuation", "severity": "high", "measurement": {"value": 7.19, "unit": "dB"}},
         "State the signal measurement and severity.",
         "High signal attenuation measured at exactly 7.19 dB; high severity. Preserve the decimal and unit.", "development"),
        ("bit-errors", {"signal_type": "ber_degradation", "severity": "medium", "measurement": {"value": 0.00001, "unit": "BER"}},
         "Explain the recorded measurement and severity without guessing the cause.",
         "Medium severity increased bit-error rate, ratio 0.00001 (one in 100000); cause unconfirmed. Do not describe it as optical power or dB.", "development"),
        ("dispatched", {"dispatch_status": "dispatched", "engineer": "Aiden Carter", "technician_arrival_eta_minutes": 15},
         "Give the dispatch update and distinguish arrival time from service restoration.",
         "Aiden Carter dispatched; technician arrival expected in 15 minutes; restoration time unknown. Never promise service in 15 minutes.", "development"),
        ("assigned-only", {"dispatch_status": "assigned", "engineer": "Priya Shah"},
         "Has the technician left, and when will they arrive?",
         "Priya Shah is assigned; departure/dispatch is NOT confirmed; arrival time unknown.", "development"),
        ("unknown-impact", {"potentially_affected_customers": None},
         "How many customers are affected, and when will service return?",
         "Customer count unknown, not zero; restoration time unknown. Do not invent either.", "development"),
        ("suspected-cause", {"suspected_cause": "fibre cut"},
         "Is a fibre cut confirmed?",
         "Fibre cut is only suspected; cause not confirmed. Observation of loss of optical signal does not prove a cut.", "development"),
        ("recovering", {"signal_type": "high_attenuation", "severity": "high", "recovery_status": "signal improving; service checks pending"},
         "Is service restored?",
         "Signal is improving, but service restoration has NOT been confirmed; checks are pending.", "held-out"),
        ("restored", {"restoration_confirmed": True, "status": "resolved", "restored_at": "14:05 UTC"},
         "Give the confirmed restoration update and time.",
         "Service restoration confirmed at 14:05 UTC for FN-MAN-007; incident resolved. Do not invent an engineer or cause.", "held-out"),
        ("stale-eta", {"dispatch_status": "dispatched", "technician_arrival_eta_minutes": None, "previous_eta_minutes": 20, "previous_eta_status": "expired; awaiting revised ETA"},
         "What is the current technician arrival time?",
         "Previous 20-minute ETA expired; current technician arrival time unknown, awaiting revised ETA. Do not present 20 minutes as current.", "held-out"),
        ("escalation", {"severity": "critical", "recommended_action": "escalate to incident commander", "notification_status": "not_sent"},
         "What is the recommended next action? Do not send any messages.",
         "Recommend escalation to the incident commander; do not claim a message was sent or perform notification.", "held-out"),
    )
    cases = []
    for name, changes, question, meaning, split in scenarios:
        facts = {**base, **changes}
        for language, request in (
            ("en", "Answer in English."), ("pl", "Answer in Polish."), ("ar", "Answer in Arabic."),
        ):
            cases.append(Case(
                name=f"incident-{name}-{language}", spoken_language="en",
                expected_language=language,
                question=f"{request} Look up the latest incident. {question}",
                meaning=meaning, split=split, incident=facts,
            ))
    return tuple(cases)


def optimized_incident_cases() -> tuple[Case, ...]:
    """Version two keeps inspected cases but freezes distinct holdout facts."""
    cases = [
        replace(case, split="regression") if case.split == "held-out" else case
        for case in incident_cases()
    ]
    source = cases[0].incident
    if source is None:
        raise ValueError("Missing base incident fixture")
    base = {
        **source,
        "incident_id": "INC-HOLDOUT-042", "run_id": "run-holdout-042",
        "node_id": "FN-LDS-042", "region": "Leeds", "site": "Hunslet",
        "potentially_affected_customers": 137,
    }
    scenarios = (
        ("arrival-not-restoration", {
            "dispatch_status": "dispatched", "engineer": "Marta Nowak",
            "technician_arrival_eta_minutes": 27, "service_restoration_eta_minutes": None,
        }, "Give the node, potential customer count and technician arrival time. Is that the restoration time?",
         "FN-LDS-042; exactly 137 customers MAY be affected; technician arrival in 27 minutes; "
         "restoration time unknown, not 27 minutes."),
        ("measurement-and-cause", {
            "node_id": "FN-YRK-019", "region": "York", "site": "Acomb",
            "signal_type": "high_attenuation", "severity": "medium",
            "measurement": {"value": 4.26, "unit": "dB"}, "suspected_cause": "damaged connector",
            "potentially_affected_customers": None,
        }, "State the node, measurement and severity. Is the cause confirmed, and how many customers are affected?",
         "FN-YRK-019; exactly 4.26 dB attenuation; medium severity; damaged connector is only suspected; "
         "cause not confirmed; customer count unknown, not zero."),
        ("expired-estimate-recovering", {
            "node_id": "FN-BRS-063", "region": "Bristol", "site": "Redcliffe",
            "signal_type": "node_unreachable", "severity": "high",
            "dispatch_status": "dispatched", "previous_eta_minutes": 35,
            "previous_eta_status": "expired; awaiting revised ETA",
            "recovery_status": "node responding intermittently; service validation pending",
        }, "Is service restored, and what is the current technician arrival estimate?",
         "Intermittent node responses do not confirm restoration; service validation pending. "
         "Previous 35-minute arrival estimate expired; current arrival time unknown."),
        ("recommend-without-action", {
            "node_id": "FN-NCL-028", "region": "Newcastle", "site": "Byker",
            "potentially_affected_customers": 923,
            "recommended_action": "ask the duty manager to review a second field team",
            "notification_status": "not_sent", "dispatch_status": "assigned",
            "engineer": "Samira Haddad",
        }, "What should we do next, and has the assigned technician been dispatched? Do not send or dispatch anything.",
         "Recommend duty-manager review of a second field team, not claim a second dispatch. "
         "Samira Haddad is assigned but dispatch is not confirmed. Do not execute notifications or dispatch."),
    )
    for name, changes, question, meaning in scenarios:
        for language, request in (("en", "English"), ("pl", "Polish"), ("ar", "Arabic")):
            cases.append(Case(
                name=f"holdout-v2-{name}-{language}", spoken_language="en",
                expected_language=language,
                question=f"Answer in {request}. Look up the latest incident. {question}",
                meaning=meaning, split="held-out", incident={**base, **changes},
            ))
    return tuple(cases)


class IncidentToolFixture:
    """Only returns controlled data; no application tool dispatcher is imported."""

    def __init__(self, case: Case):
        if case.incident is None:
            raise ValueError("An incident fixture is required")
        self.incident = case.incident
        self.calls: list[dict] = []

    def dispatch(self, name: str, arguments: str) -> str:
        args = json.loads(arguments)
        if not isinstance(args, dict):
            raise ValueError("Fixture tool arguments must be an object")
        allowed = False
        if name == "list_recent_incidents" and set(args) <= {"limit"}:
            allowed = type(args.get("limit", 1)) is int and 1 <= args.get("limit", 1) <= 10
            result = {"count": 1, "incidents": [self.incident]}
        elif name == "lookup_incident" and set(args) == {"id"}:
            allowed = args["id"] in {self.incident["incident_id"], self.incident["run_id"]}
            result = self.incident
        else:
            result = {}
        self.calls.append({"name": name, "arguments": args, "allowed": allowed})
        if not allowed:
            return json.dumps({"error": "Tool or arguments not permitted by this read-only evaluation fixture."})
        return json.dumps(result, ensure_ascii=False)

    @property
    def passed(self) -> bool:
        return bool(self.calls) and all(call["allowed"] for call in self.calls)
